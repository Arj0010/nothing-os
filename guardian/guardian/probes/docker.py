"""Container memory/CPU straight from cgroup v2 files — `docker stats` costs ~2 s per call."""
import subprocess
from pathlib import Path

from ..config import CGROUP_ROOT
from ..models import ContainerInfo


def container_usage(cid: str, root: Path = CGROUP_ROOT) -> tuple[int, int] | None:
    """(cpu usage_usec, anon bytes) for a running container, or None.

    anon = memory only swap can take back; memory.current also counts reclaimable page cache.
    """
    d = root / f"docker-{cid}.scope"
    try:
        mem = dict(line.split() for line in (d / "memory.stat").read_text().splitlines())
        cpu = dict(line.split() for line in (d / "cpu.stat").read_text().splitlines())
        return int(cpu["usage_usec"]), int(mem["anon"])
    except (OSError, ValueError, KeyError):
        return None


def probe_containers(run=subprocess.run, root: Path = CGROUP_ROOT) -> list[ContainerInfo]:
    try:
        p = run(["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if p.returncode != 0:
        return []
    out: list[ContainerInfo] = []
    for line in p.stdout.splitlines():
        cid, _, name = line.partition(" ")
        usage = container_usage(cid, root)
        if usage:
            out.append(ContainerInfo(id=cid, name=name, cpu_usec=usage[0], mem_bytes=usage[1]))
    return out
