from guardian.config import GUARDIAN_BIN
from guardian.protect import is_protected


def test_default_patterns():
    for name in ("cinnamon", "cinnamon-session", "Xorg", "gdm-x-session", "systemd", "sshd",
                 "pipewire-pulse", "tailscaled", "earlyoom", "claude"):
        assert is_protected(name, "", set()), name
    assert not is_protected("python3", "python3 app.py", set())


def test_extra_patterns_and_self():
    assert is_protected("node", "", {"node"})
    assert is_protected("python3", f"/x/python {GUARDIAN_BIN} run", set())
