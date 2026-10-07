"""Orchestration: one timer cycle, the asker, acting, and interactive review."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .actions import ActionContext, execute
from .advisor import advise as default_advise
from .ask import launch_asker, notify_cmd, notify_plain, open_review, parse_choice
from .models import (Advice, ContainerInfo, DeepScan, DiskFacts, Facts, Finding, History, RamFacts, Severity,
                     finding_id)
from .probes.disk import probe_disk
from .probes.docker import probe_containers
from .probes.ram import probe_ram
from .probes.scan import deep_scan
from .rules import DISK_WARN, IDLE_WINDOW, LEAK_WINDOW, evaluate, proc_key
from .state import State
from .units import human

SCAN_EVERY = 6 * 3600
SCAN_MIN_GAP_HIGH = 1800
ADVICE_EVERY = 6 * 3600
ADVICE_MIN_RAM = 30.0  # % available; `claude -p` is ~400 MB of node
EMERGENCY_GAP = 1800
DIGEST_EVERY = 86400
SAMPLE_KEEP = 25 * 3600  # a day for `guardian report`, plus slack


@dataclass
class Deps:
    probe_ram: Callable[[], RamFacts] = probe_ram
    probe_disk: Callable[[], DiskFacts] = lambda: probe_disk("/")
    probe_containers: Callable[[], list[ContainerInfo]] = probe_containers
    deep_scan: Callable[[], DeepScan] = lambda: deep_scan(Path.home())
    advise: Callable[[DeepScan], Advice] = default_advise
    launch_asker: Callable[[str], None] = launch_asker
    notify: Callable[[str, str], None] = notify_plain
    spawn_review: Callable[[], None] = open_review
    ctx: ActionContext = field(default_factory=ActionContext)
    now: Callable[[], float] = time.time


def run_once(state: State, deps: Deps, dry_run: bool = False) -> list[Finding]:
    now = deps.now()
    ram, disk, containers = deps.probe_ram(), deps.probe_disk(), deps.probe_containers()
    scan = _current_scan(state, deps, disk, now)
    hist = History(rss=state.samples("rss", now - LEAK_WINDOW - 300),
                   cpu=state.samples("cpu", now - IDLE_WINDOW - 3600))
    findings = evaluate(
        Facts(now=now, ram=ram, disk=disk, containers=containers, scan=scan), hist,
        protected=state.protected(),
        critical_unanswered_since=state.unanswered_since(finding_id("disk_high", disk.mount, Severity.critical)),
    )
    if dry_run:
        return findings
    _record_samples(state, ram, disk, containers, now)
    for f in findings:
        state.upsert_finding(f, now)
    state.resolve_missing({f.id for f in findings})
    for f in findings:
        if f.severity is Severity.emergency:
            _emergency(state, deps, f, now)
        elif f.severity in (Severity.warn, Severity.critical) and state.should_ask(f.id, now):
            state.mark_asked(f.id, now)
            deps.launch_asker(f.id)
    _digest(state, deps, findings, now)
    _maybe_advise(state, deps, ram, disk, scan, findings, now)
    return findings


def ask_flow(state: State, deps: Deps, fid: str) -> None:
    f = state.get_finding(fid)
    if f is None:
        return
    try:
        p = deps.ctx.run(notify_cmd(f), capture_output=True, text=True)  # blocks until click/expiry
        choice = parse_choice(p.stdout) if p.returncode == 0 else "timeout"
    except OSError:
        choice = "timeout"
    state.record_decision(fid, choice, deps.now())
    if choice == "do":
        act_flow(state, deps, fid)
    elif choice == "details":
        try:
            deps.spawn_review()
        except OSError:
            deps.notify("Guardian", "Run `guardian review` in a terminal.")


def act_flow(state: State, deps: Deps, fid: str) -> None:
    f = state.get_finding(fid)
    if f is None or not f.actions:
        return
    freed, failures = _run_actions(state, deps, f)
    msg = f"Freed ~{human(freed)}." if freed else "Done."
    if failures:
        msg += f" {len(failures)} step(s) refused/failed — {failures[0]}"
    deps.notify(f"Guardian: {f.title}", msg)


def review_flow(state: State, deps: Deps, input_fn=input, out=print) -> None:
    pending = state.pending()
    raw = state.meta_get("advice")
    advice = Advice.model_validate_json(raw) if raw else None
    if not pending and advice is None:
        out("Nothing pending.")
        return
    for f in pending:
        out(f"\n[{f.severity.value}] {f.title}\n  {f.reason}")
        for a in f.actions:
            out(f"    • {a.name}{' ' + a.target if a.target else ''}")
        if not f.actions:
            continue
        ans = input_fn("  Do it? [y]es / [s]kip 24h / [n]ot now: ").strip().lower()
        if ans == "y":
            state.record_decision(f.id, "do", deps.now())
            act_flow(state, deps, f.id)
        elif ans == "s":
            state.record_decision(f.id, "skip", deps.now())
    if advice:
        _print_advice(advice, out)


# --- helpers ----------------------------------------------------------------
def _run_actions(state: State, deps: Deps, f: Finding) -> tuple[int, list[str]]:
    freed, failures = 0, []
    for a in f.actions:
        r = execute(a, deps.ctx)
        label = a.name + (f":{a.target}" if a.target else "")
        state.record_history(f.id, label, r.ok, r.detail, deps.now())
        freed += r.freed_bytes
        if not r.ok:
            failures.append(f"{label}: {r.detail}")
    state.mark_done(f.id)
    return freed, failures


def _current_scan(state: State, deps: Deps, disk: DiskFacts, now: float) -> DeepScan | None:
    raw = state.meta_get("scan")
    scan = DeepScan.model_validate_json(raw) if raw else None
    age = now - scan.at if scan else float("inf")
    if age >= SCAN_EVERY or (disk.pct >= DISK_WARN and age >= SCAN_MIN_GAP_HIGH):
        try:
            scan = deps.deep_scan()
            state.meta_set("scan", scan.model_dump_json())
        except Exception:  # noqa: BLE001 - a failed scan keeps the previous one
            pass
    return scan


def _record_samples(state: State, ram: RamFacts, disk: DiskFacts, containers: list[ContainerInfo],
                    now: float) -> None:
    state.add_sample("sys", "disk", now, disk.pct)
    state.add_sample("sys", "ram", now, ram.avail_pct)
    state.add_sample("sys", "psi", now, ram.psi_some)
    for p in ram.top:
        state.add_sample("rss", proc_key(p), now, p.rss)
    for c in containers:
        state.add_sample("cpu", c.id, now, c.cpu_usec)
    state.prune_samples(now - SAMPLE_KEEP)


def _emergency(state: State, deps: Deps, f: Finding, now: float) -> None:
    if now - float(state.meta_get("last_emergency") or 0) < EMERGENCY_GAP:
        return
    state.meta_set("last_emergency", str(now))
    freed, _ = _run_actions(state, deps, f)
    deps.notify("Guardian: emergency disk cleanup", f"{f.reason} Freed ~{human(freed)}. See `guardian history`.")


def _digest(state: State, deps: Deps, findings: list[Finding], now: float) -> None:
    infos = [f for f in findings if f.severity is Severity.info]
    if not infos or now - float(state.meta_get("last_digest") or 0) < DIGEST_EVERY:
        return
    state.meta_set("last_digest", str(now))
    deps.notify(f"Guardian: {len(infos)} suggestion(s)",
                "; ".join(f.title for f in infos[:3]) + " — run `guardian review`.")


def _maybe_advise(state: State, deps: Deps, ram: RamFacts, disk: DiskFacts, scan: DeepScan | None,
                  findings: list[Finding], now: float) -> None:
    if scan is None or disk.pct < DISK_WARN or ram.avail_pct < ADVICE_MIN_RAM:
        return
    df = next((f for f in findings if f.kind == "disk_high"), None)
    if df is None or (df.actions and state.last_decision(df.id) != "skip"):
        return
    if now - float(state.meta_get("last_advice") or 0) < ADVICE_EVERY:
        return
    state.meta_set("last_advice", str(now))
    state.meta_set("advice", deps.advise(scan).model_dump_json())
    deps.notify("Guardian: disk advice ready", "Run `guardian review` to see what's using space.")


def _print_advice(advice: Advice, out) -> None:
    if advice.source == "llm" and advice.suggestions:
        out("\nAdvisor suggestions (text only — Guardian will not run these):")
        for s in advice.suggestions:
            out(f"  {human(s.size_bytes):>9}  {s.path}  [{s.safe_to_delete_confidence:.0%} safe]")
            out(f"             {s.what_it_is} — {s.how_to_clean}")
    else:
        out("\nLargest folders (advisor unavailable):")
        for d in advice.top_dirs:
            out(f"  {human(d.size_bytes):>9}  {d.path}")
