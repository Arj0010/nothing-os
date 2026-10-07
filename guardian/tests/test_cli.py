import pytest

from guardian import cli
from guardian.config import state_db
from guardian.state import State
from tests.helpers import Harness


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    h = Harness(tmp_path, disk_pct=86)
    monkeypatch.setattr(cli, "default_deps", lambda state: h.deps)
    return h


def test_scan_prints_findings_and_never_acts(harness, capsys):
    assert cli.main(["scan", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Disk 86% full" in out and "dry run" in out
    assert harness.asked == [] and harness.rec.calls == []


def test_run_launches_asker(harness):
    assert cli.main(["run"]) == 0
    assert len(harness.asked) == 1


def test_protect_and_history(harness, capsys):
    cli.main(["protect", "node"])
    assert State(state_db()).protected() == {"node"}
    State(state_db()).record_history("f", "apt_clean", True, "ok", 0)
    cli.main(["history"])
    assert "apt_clean" in capsys.readouterr().out


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as e:
        cli.main(["explode"])
    assert e.value.code != 0
