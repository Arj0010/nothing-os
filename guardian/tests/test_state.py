import multiprocessing

import pytest

from guardian.models import ActionSpec, Finding, Severity
from guardian.state import State


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "s.db")


def mk(fid="f1", sev=Severity.warn):
    return Finding(id=fid, kind="disk_high", severity=sev, title="t", reason="r")


def test_new_finding_asked_once_then_waits_2h(state):
    state.upsert_finding(mk(), now=1000)
    assert state.should_ask("f1", 1000)
    state.mark_asked("f1", 1000)
    assert not state.should_ask("f1", 1000 + 7199)
    assert state.should_ask("f1", 1000 + 7200)


def test_skip_snoozes_24h(state):
    state.upsert_finding(mk(), 1000)
    state.mark_asked("f1", 1000)
    state.record_decision("f1", "skip", 1000)
    assert not state.should_ask("f1", 1000 + 86399)
    assert state.should_ask("f1", 1000 + 86400)
    assert state.last_decision("f1") == "skip"


def test_resolved_finding_rearms_when_it_returns(state):
    state.upsert_finding(mk(), 1000)
    state.mark_asked("f1", 1000)
    state.resolve_missing(set())
    assert not state.should_ask("f1", 1001)
    state.upsert_finding(mk(), 2000)
    assert state.should_ask("f1", 2000)
    assert state.first_seen("f1") == 2000


def test_done_finding_reasks_after_2h_if_condition_persists(state):
    state.upsert_finding(mk(), 1000)
    state.mark_asked("f1", 1000)
    state.mark_done("f1")
    state.upsert_finding(mk(), 1100)
    assert not state.should_ask("f1", 1100)
    assert state.should_ask("f1", 1000 + 7200)


def test_unanswered_since(state):
    state.upsert_finding(mk("c", Severity.critical), 1000)
    assert state.unanswered_since("c") == 1000
    state.record_decision("c", "timeout", 1100)
    assert state.unanswered_since("c") == 1000
    state.record_decision("c", "skip", 1200)
    assert state.unanswered_since("c") is None
    assert state.unanswered_since("missing") is None


def test_old_decisions_do_not_count_after_finding_returns(state):
    state.upsert_finding(mk("c", Severity.critical), 1000)
    state.record_decision("c", "do", 1100)
    state.resolve_missing(set())
    state.upsert_finding(mk("c", Severity.critical), 5000)
    assert state.unanswered_since("c") == 5000


def test_samples_grouped_and_pruned(state):
    state.add_sample("rss", "k1", 1, 10)
    state.add_sample("rss", "k1", 2, 20)
    state.add_sample("rss", "k2", 2, 5)
    assert state.samples("rss", 0) == {"k1": [(1, 10), (2, 20)], "k2": [(2, 5)]}
    state.prune_samples(2)
    assert state.samples("rss", 0) == {"k1": [(2, 20)], "k2": [(2, 5)]}


def test_finding_roundtrip_and_pending(state):
    f = mk()
    f.actions = [ActionSpec(name="docker_builder_prune")]
    state.upsert_finding(f, 1)
    assert state.get_finding("f1") == f
    assert state.pending() == [f]
    state.mark_done("f1")
    assert state.pending() == []


def test_meta_protected_history(state):
    assert state.meta_get("x") is None
    state.meta_set("x", "1")
    state.meta_set("x", "2")
    assert state.meta_get("x") == "2"
    state.add_protected("node")
    state.add_protected("node")
    assert state.protected() == {"node"}
    state.record_history("f1", "apt_clean", True, "ok", 5)
    assert state.history() == [(5, "f1", "apt_clean", 1, "ok")]


def _writer(path, key):
    s = State(path)
    for i in range(200):
        s.add_sample("rss", key, i, i)


def test_concurrent_processes_do_not_lock(tmp_path):
    path = tmp_path / "s.db"
    State(path)
    # spawn = fresh interpreters, like systemd-launched runs/askers. Forking with open SQLite
    # connections is unsupported by SQLite and guardian never does it.
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(path, k)) for k in "abcd"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    assert [p.exitcode for p in procs] == [0, 0, 0, 0]
    got = State(path).samples("rss", 0)
    assert {k: len(got.get(k, [])) for k in "abcd"} == dict.fromkeys("abcd", 200)
