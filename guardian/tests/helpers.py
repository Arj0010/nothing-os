import subprocess


class Recorder:
    """Fake subprocess.run: records calls; replies by substring match on the joined command."""

    def __init__(self, responses=None, exc=None):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.responses = responses or {}
        self.exc = exc

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        self.kwargs.append(kw)
        if self.exc:
            raise self.exc
        joined = " ".join(cmd)
        for key, (out, rc) in self.responses.items():
            if key in joined:
                return subprocess.CompletedProcess(cmd, rc, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def ran(self, *prefix) -> bool:
        return any(c[: len(prefix)] == list(prefix) for c in self.calls)


def fake_cgroup(root, cid, mem=300_000_000, usec=1000):
    d = root / f"docker-{cid}.scope"
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.stat").write_text(f"anon {mem}\nfile 999999999\n")
    (d / "cpu.stat").write_text(f"usage_usec {usec}\nuser_usec 1\nsystem_usec 1\n")
    return d


# --- flow harness -------------------------------------------------------------
from guardian.actions import ActionContext  # noqa: E402
from guardian.flows import Deps  # noqa: E402
from guardian.models import Advice, DeepScan, DiskFacts, RamFacts, Reclaimable  # noqa: E402

GB = 10**9


class Clock:
    def __init__(self, t: float = 100_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Harness:
    """Deps wired to fakes; knobs (disk_pct, avail_pct, top, reclaim) can change between runs."""

    def __init__(self, home, disk_pct=50.0, avail_pct=50.0, reclaim=None, rec=None):
        self.clock = Clock()
        self.disk_pct, self.avail_pct, self.top = disk_pct, avail_pct, []
        self.reclaim = reclaim or Reclaimable()
        self.rec = rec or Recorder()
        self.asked: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.scans = self.advice = self.reviews = 0
        self.deps = Deps(
            probe_ram=self._ram, probe_disk=self._disk, probe_containers=lambda: [],
            deep_scan=self._scan, advise=self._advise, launch_asker=self.asked.append,
            notify=lambda t, b: self.notes.append((t, b)), spawn_review=self._review,
            ctx=ActionContext(home=home, run=self.rec, protected=set(), kill_grace=1, sleep=lambda s: None),
            now=self.clock,
        )

    def _ram(self):
        return RamFacts(total=10 * GB, available=int(10 * GB * self.avail_pct / 100), swap_total=0,
                        swap_free=0, top=list(self.top))

    def _disk(self):
        used = int(100 * GB * self.disk_pct / 100)
        return DiskFacts(mount="/", used=used, avail=100 * GB - used)

    def _scan(self):
        self.scans += 1
        return DeepScan(at=self.clock(), reclaim=self.reclaim)

    def _advise(self, scan):
        self.advice += 1
        return Advice(at=self.clock(), source="fallback", top_dirs=scan.dirs)

    def _review(self):
        self.reviews += 1
