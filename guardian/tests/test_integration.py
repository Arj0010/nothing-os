"""Real Docker container and real process: run with `pytest -m integration`."""
import os
import subprocess
import sys
import time

import psutil
import pytest

from guardian import rules
from guardian.actions import ActionContext, execute
from guardian.models import ActionSpec, DiskFacts, Facts, History, ProcInfo, RamFacts
from guardian.probes.docker import probe_containers
from guardian.rules import evaluate, proc_key

pytestmark = pytest.mark.integration


def test_probe_and_stop_idle_container_for_real():
    cid = subprocess.run(["docker", "run", "-d", "--rm", "alpine", "sleep", "600"],
                         capture_output=True, text=True, check=True).stdout.strip()
    try:
        time.sleep(2)
        [c] = [c for c in probe_containers() if c.id == cid]
        assert c.cpu_usec > 0 and c.mem_bytes >= 0
        r = execute(ActionSpec(name="stop_container", target=cid), ActionContext())
        assert r.ok, r.detail
        running = subprocess.run(["docker", "ps", "-q", "--no-trunc"], capture_output=True, text=True).stdout
        assert cid not in running
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_busy_container_is_not_stopped():
    cid = subprocess.run(["docker", "run", "-d", "--rm", "alpine", "sh", "-c", "while :; do :; done"],
                         capture_output=True, text=True, check=True).stdout.strip()
    try:
        time.sleep(1)
        r = execute(ActionSpec(name="stop_container", target=cid), ActionContext())
        assert not r.ok and "busy" in r.detail
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_leak_detected_and_killed_on_real_process(monkeypatch):
    monkeypatch.setattr(rules, "LEAK_BYTES", 50 * 10**6)
    hog = subprocess.Popen([sys.executable, "-c",
                            "import time\nbuf=[]\nwhile True:\n    buf.append(b'x' * 30_000_000)\n    time.sleep(1)"])
    try:
        ps = psutil.Process(hog.pid)
        hist, flagged = History(), []
        for _ in range(8):
            time.sleep(1.2)
            p = ProcInfo(pid=hog.pid, start_time=ps.create_time(), name=ps.name(),
                         cmdline=" ".join(ps.cmdline()), rss=ps.memory_info().rss, uid=os.getuid())
            now = time.time()
            facts = Facts(now=now, ram=RamFacts(total=10**12, available=10**12, swap_total=0, swap_free=0,
                                                top=[p]),
                          disk=DiskFacts(mount="/", used=1, avail=99))
            flagged += [f for f in evaluate(facts, hist, protected=set(), critical_unanswered_since=None)
                        if f.kind == "leak_suspect"]
            hist.rss.setdefault(proc_key(p), []).append((now, float(p.rss)))
            if flagged:
                break
        assert flagged, "growing process was not flagged"
        r = execute(flagged[0].actions[0], ActionContext(kill_grace=2))
        assert r.ok, r.detail
        assert hog.wait(5) is not None
    finally:
        if hog.poll() is None:
            hog.kill()
