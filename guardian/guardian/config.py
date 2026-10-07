"""Fixed paths and maps. Nothing here is user-configurable in Phase 1."""
import os
import sys
from pathlib import Path

HELPER = "/usr/local/sbin/guardian-root-helper"
# Console script next to the venv interpreter (not resolved: .venv/bin/python is a symlink).
GUARDIAN_BIN = Path(sys.executable).with_name("guardian")
CGROUP_ROOT = Path("/sys/fs/cgroup/system.slice")  # docker-<id>.scope lives here
PSI_MEMORY = Path("/proc/pressure/memory")

# The only directories clear_user_cache may delete, relative to $HOME.
CACHE_PATHS: dict[str, str] = {
    "pip": ".cache/pip",
    "uv": ".cache/uv",
    "npm": ".npm/_cacache",
    "ms-playwright": ".cache/ms-playwright",
    "thumbnails": ".cache/thumbnails",
    "gradle": ".gradle/caches",
}


def state_db() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "guardian" / "state.db"
