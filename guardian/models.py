from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    info = "info"
    warn = "warn"
    critical = "critical"
    emergency = "emergency"


class ProcInfo(BaseModel):
    pid: int
    start_time: float
    name: str
    cmdline: str = ""
    rss: int
    uid: int


class RamFacts(BaseModel):
    total: int
    available: int
    swap_total: int
    swap_free: int
    psi_some: float = 0.0  # /proc/pressure/memory "some avg60" (% of time any task stalled)
    psi_full: float = 0.0  # "full avg60" (% of time all tasks stalled)
    top: list[ProcInfo] = Field(default_factory=list)

    @property
    def avail_pct(self) -> float:
        return 100.0 * self.available / self.total if self.total else 100.0


class DiskFacts(BaseModel):
    mount: str
    used: int
    avail: int

    @property
    def pct(self) -> float:  # same formula as df: used / (used + avail)
        total = self.used + self.avail
        return 100.0 * self.used / total if total else 0.0


class ContainerInfo(BaseModel):
    id: str  # full 64-hex id
    name: str
    cpu_usec: int  # cumulative CPU time from cgroup cpu.stat
    mem_bytes: int


class Reclaimable(BaseModel):
    build_cache: int = 0
    dangling_images: int = 0  # count, not bytes
    user_caches: dict[str, int] = Field(default_factory=dict)
    journal: int = 0
    apt_cache: int = 0
    rotated_logs: int = 0


class DirSize(BaseModel):
    path: str
    size_bytes: int
    mtime: float


class DeepScan(BaseModel):
    at: float
    reclaim: Reclaimable
    dirs: list[DirSize] = Field(default_factory=list)


class Facts(BaseModel):
    now: float
    ram: RamFacts
    disk: DiskFacts
    containers: list[ContainerInfo] = Field(default_factory=list)
    scan: DeepScan | None = None


class History(BaseModel):
    rss: dict[str, list[tuple[float, float]]] = Field(default_factory=dict)  # proc_key -> [(t, rss)]
    cpu: dict[str, list[tuple[float, float]]] = Field(default_factory=dict)  # container id -> [(t, cpu_usec)]


class ActionSpec(BaseModel):
    name: str
    target: str | None = None
    fingerprint: dict[str, str | int | float] = Field(default_factory=dict)


class Finding(BaseModel):
    id: str
    kind: str
    severity: Severity
    title: str
    reason: str
    est_saving_bytes: int = 0
    actions: list[ActionSpec] = Field(default_factory=list)


class ActionResult(BaseModel):
    ok: bool
    detail: str
    freed_bytes: int = 0


class AdvisorSuggestion(BaseModel):
    path: str
    size_bytes: int
    what_it_is: str
    safe_to_delete_confidence: float = Field(ge=0, le=1)
    how_to_clean: str


class Advice(BaseModel):
    at: float
    source: str  # "llm" | "fallback"
    suggestions: list[AdvisorSuggestion] = Field(default_factory=list)
    top_dirs: list[DirSize] = Field(default_factory=list)


def finding_id(kind: str, key: str, severity: Severity) -> str:
    return hashlib.sha1(f"{kind}|{key}|{severity.value}".encode()).hexdigest()[:12]
