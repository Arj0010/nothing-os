"""Load/overhead tests on the real machine: run with `pytest -m stress`.

Memory pressure is created inside its own cgroup (MemoryHigh) so the rest of the system is unaffected.
"""
import multiprocessing
import os
import resource
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from guardian.models import DeepScan, Finding, Reclaimable, Severity
from guardian.probes.ram import probe_ram, read_psi
from guardian.state import State
from guardian.flows import run_once
from tests.helpers import Harness

pytestmark = pytest.mark.stress
ROOT = Path(__file__).parents[1]
GUARDIAN = ROOT / ".venv" / "bin" / "guardian"


def test_real_memory_pressure_shows_up_in_psi():
    """A hog squeezed by MemoryHigh makes the kernel reclaim into zram -> PSI must register it."""
    unit = f"guardian-stress-{uuid.uuid4().hex[:8]}"
    hog = "import time\nb=[bytearray(b'x'*(10*2**20)) for _ in range(40)]\ntime.sleep(60)"  # 400 MB, touched
    subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                    "-p", "MemoryHigh=150M", "-p", "MemoryMax=600M", sys.executable, "-c", hog], check=True)
    try:
        cg = subprocess.run(["systemctl", "--user", "show", "-P", "ControlGroup", unit],
                            capture_output=True, text=True, check=True).stdout.strip()
        pressure = Path("/sys/fs/cgroup") / cg.lstrip("/") / "memory.pressure"
        some = 0.0
        for _ in range(20):
            time.sleep(1)
            if pressure.exists():
                some, _full = read_psi(pressure)
                if some > 0:
                    break
        assert some > 0, f"no PSI registered in {pressure}"
        t = time.perf_counter()
        probe_ram()
        assert time.perf_counter() - t < 1.0, "probe slowed down under memory pressure"
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)


def test_per_run_overhead_budget(tmp_path):
    """Real `guardian scan` end to end (real probes, cached deep scan): <0.5 s and <80 MB each."""
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path))
    db = State(tmp_path / "guardian" / "state.db")
    db.meta_set("scan", DeepScan(at=time.time(), reclaim=Reclaimable()).model_dump_json())
    walls = []
    for _ in range(10):
        t = time.perf_counter()
        subprocess.run([str(GUARDIAN), "scan"], env=env, capture_output=True, check=True)
        walls.append(time.perf_counter() - t)
    peak_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    print(f"wall max={max(walls):.2f}s mean={sum(walls)/len(walls):.2f}s peak={peak_mb:.0f}MB")
    assert max(walls) < 0.5
    assert peak_mb < 80


def test_probe_ram_with_300_extra_processes():
    procs = [subprocess.Popen(["sleep", "30"]) for _ in range(300)]
    try:
        t = time.perf_counter()
        r = probe_ram()
        assert time.perf_counter() - t < 0.5
        assert len(r.top) == 15
    finally:
        for p in procs:
            p.kill()
        for p in procs:
            p.wait()


def test_run_once_with_a_full_day_of_history(tmp_path):
    state = State(tmp_path / "s.db")
    h = Harness(tmp_path, disk_pct=50)
    now = h.clock()
    rows = [("rss", f"{pid}:1.0", now - i * 120, 1e8) for i in range(720) for pid in range(15)]
    rows += [("cpu", f"c{n}", now - i * 120, 1e6) for i in range(720) for n in range(7)]
    state.db.executemany("INSERT INTO samples VALUES (?,?,?,?)", rows)
    t = time.perf_counter()
    for _ in range(10):
        h.clock.t += 120
        run_once(state, h.deps)
    assert (time.perf_counter() - t) / 10 < 0.1


def _mixed_worker(path, n):
    s = State(Path(path))
    f = Finding(id=f"w{n:011d}", kind="disk_high", severity=Severity.warn, title="t", reason="r")
    for i in range(300):
        s.upsert_finding(f, i)
        s.should_ask(f.id, i)
        s.record_decision(f.id, "timeout", i)
        s.add_sample("rss", f"k{n}", i, i)
        s.resolve_missing({f.id})


def test_eight_processes_hammer_state_db(tmp_path):
    path = tmp_path / "s.db"
    State(path)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_mixed_worker, args=(str(path), n)) for n in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0] * 8
    got = State(path).samples("rss", 0)
    assert all(len(got[f"k{n}"]) == 300 for n in range(8))
