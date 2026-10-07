from fnmatch import fnmatch

from .config import GUARDIAN_BIN

DEFAULT_PROTECTED: tuple[str, ...] = (
    "cinnamon*", "Xorg", "gdm*", "systemd*", "sshd", "dbus-daemon", "pipewire*", "wireplumber",
    "tailscaled", "earlyoom", "claude", "guardian",
)


def is_protected(name: str, cmdline: str, extra: set[str]) -> bool:
    if any(fnmatch(name, pat) for pat in (*DEFAULT_PROTECTED, *extra)):
        return True
    return str(GUARDIAN_BIN) in cmdline  # never kill ourselves (python3 .venv/bin/guardian ...)
