import json
import os
import re
import subprocess
import time
from pathlib import Path

from ..config import CACHE_PATHS
from ..fsutil import dir_size
from ..models import DeepScan, DirSize, Reclaimable
from ..units import parse_size

JOURNAL_RE = re.compile(r"take up ([0-9.]+[KMGT]?)")
ROTATED_AGE = 7 * 86400


def _out(run, args: list[str], timeout: int = 60) -> str:
    try:
        p = run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout or ""


def docker_build_cache(run) -> int:
    for line in _out(run, ["docker", "system", "df", "--format", "{{json .}}"]).splitlines():
        try:
            d = json.loads(line)
            if d.get("Type") == "Build Cache":
                return parse_size(d["Reclaimable"].split()[0])
        except (ValueError, KeyError, IndexError):
            continue
    return 0


def dangling_images(run) -> int:
    out = _out(run, ["docker", "images", "-f", "dangling=true", "-q"])
    return len([line for line in out.splitlines() if line.strip()])


def journal_size(run) -> int:
    m = JOURNAL_RE.search(_out(run, ["journalctl", "--disk-usage"]))
    if not m:
        return 0
    try:
        return parse_size(m.group(1))
    except ValueError:
        return 0


def rotated_logs(log_dir: Path, now: float) -> int:
    total = 0
    for root, _dirs, files in os.walk(log_dir, onerror=lambda e: None):
        for name in files:
            if not (name.endswith(".gz") or name.endswith(".1")):
                continue
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if st.st_mtime < now - ROTATED_AGE:
                total += st.st_size
    return total


def top_dirs(home: Path, run, limit: int = 30) -> list[DirSize]:
    out: list[DirSize] = []
    for line in _out(run, ["du", "-x", "-b", "-d", "2", str(home)], timeout=180).splitlines():
        size, _, path = line.partition("\t")
        if not path or path == str(home):
            continue
        try:
            n = int(size)
        except ValueError:
            continue
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = 0.0
        out.append(DirSize(path=path, size_bytes=n, mtime=mtime))
    out.sort(key=lambda d: d.size_bytes, reverse=True)
    return out[:limit]


def deep_scan(home: Path, run=subprocess.run, now: float | None = None,
              apt_dir: Path = Path("/var/cache/apt/archives"), log_dir: Path = Path("/var/log")) -> DeepScan:
    now = time.time() if now is None else now
    caches = {name: dir_size(home / rel) for name, rel in CACHE_PATHS.items()
              if (home / rel).is_dir() and not (home / rel).is_symlink()}
    reclaim = Reclaimable(
        build_cache=docker_build_cache(run),
        dangling_images=dangling_images(run),
        user_caches=caches,
        journal=journal_size(run),
        apt_cache=dir_size(apt_dir) if apt_dir.is_dir() else 0,
        rotated_logs=rotated_logs(log_dir, now),
    )
    return DeepScan(at=now, reclaim=reclaim, dirs=top_dirs(home, run))
