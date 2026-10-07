import os
from pathlib import Path


def dir_size(path: Path) -> int:
    """Total bytes of regular files under path. Never follows symlinks; unreadable entries are skipped."""
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total
