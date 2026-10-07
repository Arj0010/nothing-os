"""Pure policy: facts + history -> findings. No I/O."""
from __future__ import annotations

import os

from .models import ActionSpec, Facts, Finding, History, ProcInfo, Reclaimable, Severity, finding_id
from .protect import is_protected
from .units import human

GB = 10**9
MB = 10**6

DISK_INFO, DISK_WARN, DISK_CRIT, DISK_EMERG = 80.0, 85.0, 92.0, 95.0
EMERGENCY_WAIT = 600
RAM_WARN, RAM_CRIT = 20.0, 10.0  # % MemAvailable
PSI_WARN, PSI_CRIT = 10.0, 5.0   # kernel memory PSI: "some" avg60 / "full" avg60, % of time stalled
LEAK_BYTES, LEAK_WINDOW = 1.5 * GB, 1800
IDLE_CPU, IDLE_WINDOW, IDLE_MIN_MEM = 1.0, 6 * 3600, 100 * MB
BUNDLE_MIN = 100 * MB
JOURNAL_MIN, JOURNAL_KEEP = 500 * MB, 200 * MB
ROTATED_MIN = 50 * MB

# Runs without a click only on the disk emergency path.
ZERO_RISK: tuple[ActionSpec, ...] = (
    ActionSpec(name="docker_builder_prune"),
    ActionSpec(name="clear_user_cache", target="pip"),
    ActionSpec(name="clear_user_cache", target="uv"),
    ActionSpec(name="clear_user_cache", target="npm"),
    ActionSpec(name="apt_clean"),
    ActionSpec(name="journal_vacuum"),
    ActionSpec(name="rotated_logs_clean"),
)


def proc_key(p: ProcInfo) -> str:
    return f"{p.pid}:{p.start_time}"


def evaluate(facts: Facts, hist: History, protected: set[str],
             critical_unanswered_since: float | None, my_uid: int | None = None) -> list[Finding]:
    uid = os.getuid() if my_uid is None else my_uid
    return [
        *_disk(facts, critical_unanswered_since),
        *_ram(facts, protected, uid),
        *_leaks(facts, hist, protected, uid),
        *_idle(facts, hist),
    ]


def bundle_actions(r: Reclaimable) -> tuple[list[ActionSpec], int]:
    acts: list[ActionSpec] = []
    total = 0
    if r.build_cache >= BUNDLE_MIN:
        acts.append(ActionSpec(name="docker_builder_prune"))
        total += r.build_cache
    if r.dangling_images > 0:
        acts.append(ActionSpec(name="docker_image_prune_dangling"))
    for name, size in sorted(r.user_caches.items()):
        if size >= BUNDLE_MIN:
            acts.append(ActionSpec(name="clear_user_cache", target=name))
            total += size
    if r.apt_cache >= BUNDLE_MIN:
        acts.append(ActionSpec(name="apt_clean"))
        total += r.apt_cache
    if r.journal >= JOURNAL_MIN:
        acts.append(ActionSpec(name="journal_vacuum"))
        total += r.journal - JOURNAL_KEEP
    if r.rotated_logs >= ROTATED_MIN:
        acts.append(ActionSpec(name="rotated_logs_clean"))
        total += r.rotated_logs
    return acts, total


def _killable(facts: Facts, protected: set[str], uid: int) -> list[ProcInfo]:
    return [p for p in sorted(facts.ram.top, key=lambda p: p.rss, reverse=True)
            if p.uid == uid and not is_protected(p.name, p.cmdline, protected)]


def _kill_action(p: ProcInfo) -> ActionSpec:
    return ActionSpec(name="kill_process", target=str(p.pid),
                      fingerprint={"pid": p.pid, "start_time": p.start_time, "name": p.name})


def _disk(facts: Facts, since: float | None) -> list[Finding]:
    d = facts.disk
    pct = d.pct
    if pct < DISK_INFO:
        return []
    acts, total = bundle_actions(facts.scan.reclaim) if facts.scan else ([], 0)
    sev = Severity.critical if pct >= DISK_CRIT else Severity.warn if pct >= DISK_WARN else Severity.info
    tail = f" Can free ~{human(total)} safely." if acts else " Nothing safe to auto-clean — see `guardian review`."
    out = [Finding(
        id=finding_id("disk_high", d.mount, sev), kind="disk_high", severity=sev,
        title=f"Disk {pct:.0f}% full",
        reason=f"{d.mount} is {pct:.0f}% full ({human(d.avail)} free).{tail}",
        est_saving_bytes=total, actions=acts,
    )]
    if pct >= DISK_EMERG and since is not None and facts.now - since >= EMERGENCY_WAIT:
        out.append(Finding(
            id=finding_id("disk_emergency", d.mount, Severity.emergency), kind="disk_emergency",
            severity=Severity.emergency, title=f"Disk {pct:.0f}% — emergency cleanup",
            reason=f"{d.mount} is ≥{DISK_EMERG:.0f}% full and was unanswered for 10 min; ran zero-risk cleanup.",
            actions=[a.model_copy() for a in ZERO_RISK],
        ))
    return out


def _ram(facts: Facts, protected: set[str], uid: int) -> list[Finding]:
    r = facts.ram
    pct = r.avail_pct
    if pct < RAM_CRIT or r.psi_full >= PSI_CRIT:
        sev = Severity.critical
    elif pct < RAM_WARN or r.psi_some >= PSI_WARN:
        sev = Severity.warn
    else:
        return []
    head = f"{human(r.available)} RAM available ({pct:.0f}%)."
    if r.psi_some >= PSI_WARN or r.psi_full >= PSI_CRIT:
        head += f" Tasks stall on memory {r.psi_some:.0f}% of the time (kernel PSI) — swap thrashing."
    cands = _killable(facts, protected, uid)
    if cands:
        hog = cands[0]
        reason, acts, est = (f"{head} Biggest: {hog.name} (pid {hog.pid}) using {human(hog.rss)}.",
                             [_kill_action(hog)], hog.rss)
    else:
        reason, acts, est = f"{head} No killable process of yours found.", [], 0
    return [Finding(id=finding_id("ram_low", "mem", sev), kind="ram_low", severity=sev,
                    title=f"Memory pressure: {pct:.0f}% available", reason=reason, est_saving_bytes=est,
                    actions=acts)]


def _leaks(facts: Facts, hist: History, protected: set[str], uid: int) -> list[Finding]:
    out = []
    for p in _killable(facts, protected, uid):
        key = proc_key(p)
        pts = [s for s in hist.rss.get(key, []) if s[0] >= facts.now - LEAK_WINDOW] + [(facts.now, float(p.rss))]
        if len(pts) < 3:
            continue
        growth = pts[-1][1] - pts[0][1]
        if growth > LEAK_BYTES and pts[-1][1] > pts[-2][1]:
            minutes = int((pts[-1][0] - pts[0][0]) / 60)
            out.append(Finding(
                id=finding_id("leak_suspect", key, Severity.warn), kind="leak_suspect", severity=Severity.warn,
                title=f"Possible memory leak: {p.name}",
                reason=(f"{p.name} (pid {p.pid}) grew {human(int(growth))} in {minutes} min and is still "
                        f"growing (now {human(p.rss)})."),
                est_saving_bytes=p.rss, actions=[_kill_action(p)],
            ))
    return out


def _idle(facts: Facts, hist: History) -> list[Finding]:
    out = []
    low_ram = facts.ram.avail_pct < RAM_WARN
    for c in facts.containers:
        if c.mem_bytes < IDLE_MIN_MEM:
            continue
        old = [s for s in hist.cpu.get(c.id, []) if s[0] <= facts.now - IDLE_WINDOW]
        if not old:
            continue  # not watched for 6 h yet
        t0, usec0 = old[-1]
        cpu_pct = (c.cpu_usec - usec0) / ((facts.now - t0) * 1e6) * 100
        if not 0 <= cpu_pct < IDLE_CPU:  # negative = counter reset (container restarted) = activity
            continue
        sev = Severity.warn if low_ram else Severity.info
        out.append(Finding(
            id=finding_id("container_idle", c.id, sev), kind="container_idle", severity=sev,
            title=f"Idle container: {c.name}",
            reason=(f"{c.name} averaged {cpu_pct:.2f}% CPU over 6+ h and holds {human(c.mem_bytes)} RAM. "
                    f"`docker start {c.name}` brings it back."),
            est_saving_bytes=c.mem_bytes,
            actions=[ActionSpec(name="stop_container", target=c.id, fingerprint={"name": c.name})],
        ))
    return out
