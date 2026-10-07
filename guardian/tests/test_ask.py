import subprocess

from guardian.ask import launch_asker, notify_cmd, notify_plain, parse_choice
from guardian.config import GUARDIAN_BIN
from guardian.models import ActionSpec, Finding, Severity
from tests.helpers import Recorder


def f(sev=Severity.warn, actions=True, saving=0):
    return Finding(id="abc123abc123", kind="disk_high", severity=sev, title="Disk 86% full", reason="why",
                   est_saving_bytes=saving, actions=[ActionSpec(name="apt_clean")] if actions else [])


def has(cmd, *seq):
    return any(cmd[i:i + len(seq)] == list(seq) for i in range(len(cmd)))


def test_question_has_buttons_and_stays_until_answered():
    cmd = notify_cmd(f(saving=4_100_000_000))
    assert cmd[0] == "notify-send"
    assert has(cmd, "-A", "do=Do it") and has(cmd, "-A", "skip=Skip 24h") and has(cmd, "-A", "details=Details")
    assert has(cmd, "-u", "critical") and "-t" not in cmd  # Cinnamon drops normal popups after 4 s
    assert cmd[-2] == "Guardian: Disk 86% full"
    assert "4.1 GB" in cmd[-1]


def test_no_do_button_without_actions():
    assert not has(notify_cmd(f(actions=False)), "-A", "do=Do it")




def test_parse_choice():
    assert parse_choice("do\n") == "do"
    assert parse_choice("details") == "details"
    assert parse_choice("") == "timeout"
    assert parse_choice("rm -rf") == "timeout"


def test_launch_asker_is_a_bounded_named_transient_unit():
    rec = Recorder()
    launch_asker("abc123abc123", run=rec)
    cmd = rec.calls[0]
    assert cmd[:3] == ["systemd-run", "--user", "--collect"]
    assert "--unit=guardian-ask-abc123abc123" in cmd
    assert has(cmd, "-p", "MemoryMax=100M") and has(cmd, "-p", "RuntimeMaxSec=3600")
    assert cmd[-3:] == [str(GUARDIAN_BIN), "ask", "abc123abc123"]


def test_side_effect_helpers_swallow_errors():
    launch_asker("x", run=Recorder(exc=OSError("no systemd")))
    notify_plain("t", "b", run=Recorder(exc=subprocess.TimeoutExpired("notify-send", 10)))
