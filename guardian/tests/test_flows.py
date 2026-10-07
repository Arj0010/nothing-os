import os

import pytest

from guardian.config import HELPER
from guardian.flows import act_flow, ask_flow, review_flow, run_once
from guardian.models import ActionSpec, Finding, ProcInfo, Reclaimable, Severity, finding_id
from guardian.state import State
from tests.helpers import GB, Harness, Recorder


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "state.db")


def crit_id():
    return finding_id("disk_high", "/", Severity.critical)


# --- run_once -------------------------------------------------------------
def test_warn_launches_one_asker_not_again_within_2h(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86)
    run_once(state, h.deps)
    assert h.asked == [finding_id("disk_high", "/", Severity.warn)]
    h.clock.t += 120
    run_once(state, h.deps)
    assert len(h.asked) == 1


def test_dry_run_asks_and_executes_nothing(tmp_path, state):
    h = Harness(tmp_path, disk_pct=96)
    findings = run_once(state, h.deps, dry_run=True)
    assert findings and h.asked == [] and h.rec.calls == [] and state.pending() == []


def test_emergency_after_10_unanswered_minutes_runs_zero_risk_only(tmp_path, state):
    pip = tmp_path / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "wheel").write_bytes(b"x" * 10)
    keep = tmp_path / "projects" / "keep.txt"
    keep.parent.mkdir()
    keep.write_text("mine")
    h = Harness(tmp_path, disk_pct=96)
    run_once(state, h.deps)
    assert h.asked == [crit_id()] and h.rec.calls == []
    h.clock.t += 600
    run_once(state, h.deps)
    assert h.rec.ran("docker", "builder", "prune", "-af")
    for verb in ("apt-clean", "journal-vacuum", "rotated-logs"):
        assert h.rec.ran("pkexec", HELPER, verb)
    assert not any(c[:2] == ["docker", "stop"] for c in h.rec.calls)
    assert not pip.exists() and keep.exists()
    assert any("emergency" in t for t, _ in h.notes)
    assert len(state.history()) == 7
    calls = len(h.rec.calls)
    h.clock.t += 120
    run_once(state, h.deps)
    assert len(h.rec.calls) == calls  # at most once per 30 min


def test_skip_on_critical_blocks_emergency(tmp_path, state):
    h = Harness(tmp_path, disk_pct=96)
    run_once(state, h.deps)
    state.record_decision(crit_id(), "skip", h.clock())
    h.clock.t += 600
    run_once(state, h.deps)
    assert h.rec.calls == []


def test_leak_detected_across_runs(tmp_path, state):
    h = Harness(tmp_path)
    for i, rss in enumerate((0.5, 1.2, 2.1)):
        h.top = [ProcInfo(pid=4242, start_time=1.0, name="python3", cmdline="python3 x.py",
                          rss=int(rss * GB), uid=os.getuid())]
        run_once(state, h.deps)
        if i < 2:
            h.clock.t += 600
    assert h.asked == [finding_id("leak_suspect", "4242:1.0", Severity.warn)]


def test_deep_scan_cached_6h_and_refreshed_early_when_disk_high(tmp_path, state):
    h = Harness(tmp_path)
    run_once(state, h.deps)
    h.clock.t += 120
    run_once(state, h.deps)
    assert h.scans == 1
    h.clock.t += 6 * 3600
    run_once(state, h.deps)
    assert h.scans == 2
    h.disk_pct = 86
    h.clock.t += 1800
    run_once(state, h.deps)
    assert h.scans == 3


def test_advisor_when_nothing_reclaimable_rate_limited(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86)
    run_once(state, h.deps)
    assert h.advice == 1 and state.meta_get("advice")
    assert any("advice" in t for t, _ in h.notes)
    h.clock.t += 120
    run_once(state, h.deps)
    assert h.advice == 1


def test_advisor_not_called_when_rules_can_reclaim(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86, reclaim=Reclaimable(build_cache=3 * GB))
    run_once(state, h.deps)
    assert h.advice == 0


def test_advisor_skipped_under_ram_pressure(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86, avail_pct=25)  # claude -p is ~400 MB of node
    run_once(state, h.deps)
    assert h.advice == 0


def test_info_digest_once_per_day(tmp_path, state):
    h = Harness(tmp_path, disk_pct=81)
    run_once(state, h.deps)
    assert len(h.notes) == 1 and h.asked == []
    h.clock.t += 120
    run_once(state, h.deps)
    assert len(h.notes) == 1
    h.clock.t += 86400
    run_once(state, h.deps)
    assert len(h.notes) == 2


def test_probe_crash_acts_on_nothing(tmp_path, state):
    h = Harness(tmp_path, disk_pct=96)

    def boom():
        raise RuntimeError("statvfs failed")

    h.deps.probe_disk = boom
    with pytest.raises(RuntimeError):
        run_once(state, h.deps)
    assert h.rec.calls == [] and h.asked == []


# --- act / ask / review -----------------------------------------------------
def prune_finding(state, now=1.0):
    f = Finding(id="aaaaaaaaaaaa", kind="disk_high", severity=Severity.warn, title="Disk 86% full", reason="r",
                actions=[ActionSpec(name="docker_builder_prune")])
    state.upsert_finding(f, now)
    return f


def test_act_flow_runs_actions_records_history_and_notifies(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder({"docker builder": ("Total reclaimed space: 1GB", 0)}))
    prune_finding(state)
    act_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.history()[0][2:4] == ("docker_builder_prune", 1)
    assert h.notes == [("Guardian: Disk 86% full", "Freed ~1.0 GB.")]
    assert state.pending() == []


def test_act_flow_unknown_finding_is_noop(tmp_path, state):
    h = Harness(tmp_path)
    act_flow(state, h.deps, "nope")
    assert h.rec.calls == [] and h.notes == []


def test_ask_flow_do_runs_actions(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder({"notify-send": ("do\n", 0)}))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.last_decision("aaaaaaaaaaaa") == "do"
    assert h.rec.ran("docker", "builder", "prune", "-af")


@pytest.mark.parametrize("reply", [("", 0), ("", 1)])
def test_ask_flow_timeout_or_notify_failure_never_acts(tmp_path, state, reply):
    h = Harness(tmp_path, rec=Recorder({"notify-send": reply}))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.last_decision("aaaaaaaaaaaa") == "timeout"
    assert not h.rec.ran("docker")


def test_ask_flow_notify_send_missing_never_acts(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder(exc=FileNotFoundError("notify-send")))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.last_decision("aaaaaaaaaaaa") == "timeout"


def test_ask_flow_details_opens_review_and_falls_back(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder({"notify-send": ("details\n", 0)}))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert h.reviews == 1

    def no_terminal():
        raise FileNotFoundError("x-terminal-emulator")

    h.deps.spawn_review = no_terminal
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert any("guardian review" in b for _, b in h.notes)


def test_review_flow_yes_acts_and_skip_snoozes(tmp_path, state):
    h = Harness(tmp_path)
    prune_finding(state)
    lines = []
    review_flow(state, h.deps, input_fn=lambda _: "y", out=lines.append)
    assert h.rec.ran("docker", "builder", "prune", "-af")
    assert any("Disk 86% full" in line for line in lines)

    state2 = State(tmp_path / "other.db")
    prune_finding(state2)
    review_flow(state2, h.deps, input_fn=lambda _: "s", out=lines.append)
    assert state2.last_decision("aaaaaaaaaaaa") == "skip"


def test_review_flow_nothing_pending(tmp_path, state):
    lines = []
    review_flow(state, Harness(tmp_path).deps, input_fn=lambda _: "y", out=lines.append)
    assert lines == ["Nothing pending."]
