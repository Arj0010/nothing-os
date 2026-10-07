from guardian.flows import run_once
from guardian.models import ActionSpec, Finding, Severity
from guardian.report import build_report
from guardian.state import State
from tests.helpers import Harness, Recorder

EARLYOOM_LOG = (
    "Oct 08 10:00:00 IdeaPad earlyoom[1]: sending SIGTERM when mem <=  6.00% and swap <= 40.00%,\n"
    'Oct 08 14:02:11 IdeaPad earlyoom[1]: sending SIGTERM to process 4242 uid 1000 "chrome": badness 900, VmRSS 2100 MiB\n'
)


def test_run_records_system_samples(tmp_path):
    state = State(tmp_path / "s.db")
    h = Harness(tmp_path, disk_pct=81, avail_pct=40)
    run_once(state, h.deps)
    got = state.samples("sys", 0)
    assert got["disk"][0][1] == 81.0 and got["ram"][0][1] == 40.0 and got["psi"][0][1] == 0.0


def test_report_summarises_day(tmp_path):
    state = State(tmp_path / "s.db")
    h = Harness(tmp_path, disk_pct=86)
    for _ in range(3):
        run_once(state, h.deps)
        h.clock.t += 120
    f = Finding(id="bbbbbbbbbbbb", kind="leak_suspect", severity=Severity.warn, title="Possible memory leak: node",
                reason="grew 2 GB", actions=[ActionSpec(name="kill_process")])
    state.upsert_finding(f, h.clock())
    state.record_decision("bbbbbbbbbbbb", "skip", h.clock())
    state.record_history("bbbbbbbbbbbb", "kill_process", False, "refused: process changed", h.clock())
    rec = Recorder({"earlyoom": (EARLYOOM_LOG, 0), "guardian.service": ("", 0)})

    md = build_report(state, now=h.clock(), hours=24, run=rec)

    assert "Disk used: min 86.0%, avg 86.0%, max 86.0%" in md
    assert "Guardian runs recorded: 3" in md
    assert "Disk 86% full" in md and "Possible memory leak: node" in md
    assert "skip" in md and "refused: process changed" in md
    assert 'process 4242 uid 1000 "chrome"' in md and "when mem <=" not in md  # kills only, not config
    assert "Guardian errors: none" in md


def test_report_handles_empty_day_and_missing_journal(tmp_path):
    md = build_report(State(tmp_path / "s.db"), now=1e9, hours=24, run=Recorder(exc=FileNotFoundError("journalctl")))
    assert "no samples" in md and "earlyoom kills: none" in md
