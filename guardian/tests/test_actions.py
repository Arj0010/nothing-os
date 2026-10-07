import subprocess
import sys

import psutil
import pytest

from guardian.actions import ActionContext, execute
from guardian.config import HELPER
from guardian.models import ActionSpec
from tests.helpers import Recorder, fake_cgroup

CID = "c" * 64


def ctx(home, rec=None, protected=None, sleep=lambda s: None):
    return ActionContext(home=home, run=rec or Recorder(), protected=protected or set(), kill_grace=2,
                         cgroup_root=home / "cg", sleep=sleep)


@pytest.fixture
def child():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def fp(p):
    ps = psutil.Process(p.pid)
    return {"pid": p.pid, "start_time": ps.create_time(), "name": ps.name()}


def test_unknown_action_refused(tmp_path):
    r = execute(ActionSpec(name="rm_rf_home"), ctx(tmp_path))
    assert not r.ok and "unknown action" in r.detail


# --- clear_user_cache ---------------------------------------------------
def test_clear_user_cache_removes_only_the_mapped_dir(tmp_path):
    pip = tmp_path / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "x").write_bytes(b"x" * 100)
    other = tmp_path / ".cache" / "other"
    other.mkdir()
    (other / "y").write_bytes(b"y")
    r = execute(ActionSpec(name="clear_user_cache", target="pip"), ctx(tmp_path))
    assert r.ok and r.freed_bytes == 100
    assert not pip.exists() and (other / "y").exists()


@pytest.mark.parametrize("target", ["../..", "", None, "Documents", "trash"])
def test_clear_user_cache_unknown_name_refused(tmp_path, target):
    (tmp_path / "keep").write_text("k")
    r = execute(ActionSpec(name="clear_user_cache", target=target), ctx(tmp_path))
    assert not r.ok and "unknown cache" in r.detail
    assert (tmp_path / "keep").exists()


def test_clear_user_cache_symlink_refused(tmp_path):
    elsewhere = tmp_path / "precious"
    elsewhere.mkdir()
    (elsewhere / "thesis.tex").write_text("!")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "pip").symlink_to(elsewhere)
    r = execute(ActionSpec(name="clear_user_cache", target="pip"), ctx(tmp_path))
    assert not r.ok and "symlink" in r.detail
    assert (elsewhere / "thesis.tex").exists()


def test_clear_user_cache_missing_is_ok(tmp_path):
    r = execute(ActionSpec(name="clear_user_cache", target="uv"), ctx(tmp_path))
    assert r.ok and r.freed_bytes == 0


# --- kill_process -------------------------------------------------------
def test_kill_process_kills_matching_child(tmp_path, child):
    r = execute(ActionSpec(name="kill_process", target=str(child.pid), fingerprint=fp(child)), ctx(tmp_path))
    assert r.ok, r.detail
    assert child.wait(5) is not None


def test_kill_process_refuses_recycled_pid(tmp_path, child):
    f = fp(child)
    f["start_time"] += 100
    r = execute(ActionSpec(name="kill_process", fingerprint=f), ctx(tmp_path))
    assert not r.ok and "changed" in r.detail
    assert child.poll() is None


def test_kill_process_refuses_protected(tmp_path, child):
    f = fp(child)
    r = execute(ActionSpec(name="kill_process", fingerprint=f), ctx(tmp_path, protected={f["name"]}))
    assert not r.ok and "protected" in r.detail
    assert child.poll() is None


def test_kill_process_already_gone(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    f = fp(p)
    p.wait()
    r = execute(ActionSpec(name="kill_process", fingerprint=f), ctx(tmp_path))
    assert not r.ok and ("gone" in r.detail or "changed" in r.detail)


@pytest.mark.parametrize("bad", [{}, {"pid": "x", "start_time": 1, "name": "a"},
                                 {"pid": 1, "start_time": 1, "name": "systemd"}])
def test_kill_process_bad_or_pid1_refused(tmp_path, bad):
    assert not execute(ActionSpec(name="kill_process", fingerprint=bad), ctx(tmp_path)).ok


# --- stop_container (cgroup re-check) -------------------------------------
def test_stop_container_rejects_bad_id_without_running_anything(tmp_path):
    rec = Recorder()
    r = execute(ActionSpec(name="stop_container", target="abc; rm -rf /"), ctx(tmp_path, rec))
    assert not r.ok and rec.calls == []


def test_stop_container_refuses_when_busy(tmp_path):
    fake_cgroup(tmp_path / "cg", CID, usec=0)
    burn = lambda s: fake_cgroup(tmp_path / "cg", CID, usec=1_000_000)  # 1 s CPU in the 2 s window = 50%
    rec = Recorder()
    r = execute(ActionSpec(name="stop_container", target=CID), ctx(tmp_path, rec, sleep=burn))
    assert not r.ok and "busy" in r.detail
    assert rec.calls == []


def test_stop_container_stops_when_idle(tmp_path):
    fake_cgroup(tmp_path / "cg", CID, usec=500)
    rec = Recorder()
    r = execute(ActionSpec(name="stop_container", target=CID), ctx(tmp_path, rec))
    assert r.ok
    assert rec.calls == [["docker", "stop", CID]]


def test_stop_container_not_running(tmp_path):
    r = execute(ActionSpec(name="stop_container", target=CID), ctx(tmp_path))
    assert not r.ok and "not running" in r.detail


# --- commands -----------------------------------------------------------
@pytest.mark.parametrize("name,verb", [("apt_clean", "apt-clean"), ("journal_vacuum", "journal-vacuum"),
                                       ("rotated_logs_clean", "rotated-logs")])
def test_root_actions_use_fixed_helper_verbs(tmp_path, name, verb):
    rec = Recorder()
    assert execute(ActionSpec(name=name, target="ignored; reboot"), ctx(tmp_path, rec)).ok
    assert rec.calls == [["pkexec", HELPER, verb]]


def test_builder_prune_reports_freed_bytes(tmp_path):
    rec = Recorder({"docker builder": ("Deleted ...\nTotal reclaimed space: 5.69GB\n", 0)})
    r = execute(ActionSpec(name="docker_builder_prune"), ctx(tmp_path, rec))
    assert r.ok and r.freed_bytes == 5_690_000_000
    assert rec.calls == [["docker", "builder", "prune", "-af"]]


def test_missing_command_returns_failure_not_exception(tmp_path):
    r = execute(ActionSpec(name="docker_builder_prune"), ctx(tmp_path, Recorder(exc=FileNotFoundError("docker"))))
    assert not r.ok and "failed" in r.detail


def test_nonzero_exit_is_failure(tmp_path):
    assert not execute(ActionSpec(name="apt_clean"), ctx(tmp_path, Recorder({"pkexec": ("", 126)}))).ok
