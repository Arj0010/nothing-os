import os
import subprocess

from .config import GUARDIAN_BIN
from .models import Finding
from .units import human

CHOICES = ("do", "skip", "details")


def notify_cmd(f: Finding) -> list[str]:
    body = f.reason + (f"\nEstimated saving: {human(f.est_saving_bytes)}" if f.est_saving_bytes else "")
    # Questions are always "critical": Cinnamon hides normal popups after 4 s and suppresses them over
    # fullscreen windows. Critical ones stay until answered; the asker unit's RuntimeMaxSec bounds the wait.
    cmd = ["notify-send", "-a", "Guardian", "-u", "critical", "-i", "dialog-warning"]
    if f.actions:
        cmd += ["-A", "do=Do it"]
    cmd += ["-A", "skip=Skip 24h", "-A", "details=Details", f"Guardian: {f.title}", body]
    return cmd


def parse_choice(stdout: str) -> str:
    lines = stdout.strip().splitlines()
    choice = lines[-1].strip() if lines else ""
    return choice if choice in CHOICES else "timeout"


def notify_plain(title: str, body: str, run=subprocess.run) -> None:
    try:
        run(["notify-send", "-a", "Guardian", title, body], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def launch_asker(fid: str, run=subprocess.run) -> None:
    """Detached unit blocking on the notification. systemd enforces one per finding (unit name),
    a 100 MB cap and a 1 h lifetime, so unanswered askers can't pile up in RAM."""
    try:
        run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=guardian-ask-{fid}",
             "-p", "MemoryMax=100M", "-p", "RuntimeMaxSec=3600",
             str(GUARDIAN_BIN), "ask", fid], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def open_review(spawn=subprocess.Popen) -> None:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    spawn(["x-terminal-emulator", "-e",
           f"bash -c '{GUARDIAN_BIN} review; read -p \"Press Enter to close\"'"], env=env)
