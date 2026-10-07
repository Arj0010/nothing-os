from pathlib import Path

import psutil

from ..config import PSI_MEMORY
from ..models import ProcInfo, RamFacts


def read_psi(path: Path = PSI_MEMORY) -> tuple[float, float]:
    """(some avg60, full avg60) from a kernel pressure file; (0, 0) if unavailable."""
    vals = {}
    try:
        for line in path.read_text().splitlines():
            kind, *fields = line.split()
            vals[kind] = float(dict(f.split("=") for f in fields)["avg60"])
    except (OSError, ValueError, KeyError):
        return 0.0, 0.0
    return vals.get("some", 0.0), vals.get("full", 0.0)


def probe_ram(top_n: int = 15) -> RamFacts:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    procs: list[ProcInfo] = []
    for p in psutil.process_iter(["pid", "name", "create_time", "cmdline", "memory_info", "uids"]):
        info = p.info
        if info.get("memory_info") is None or info.get("create_time") is None:
            continue  # vanished or access denied
        procs.append(ProcInfo(
            pid=info["pid"],
            start_time=info["create_time"],
            name=info["name"] or "",
            cmdline=" ".join(info["cmdline"] or [])[:300],
            rss=info["memory_info"].rss,
            uid=info["uids"].real if info["uids"] else -1,
        ))
    procs.sort(key=lambda x: x.rss, reverse=True)
    some, full = read_psi()
    return RamFacts(total=vm.total, available=vm.available, swap_total=sw.total, swap_free=sw.free,
                    psi_some=some, psi_full=full, top=procs[:top_n])
