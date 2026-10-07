"""Plain-facts daily summary for the beta run. No LLM."""
from __future__ import annotations

import subprocess
import time

from .models import DeepScan
from .state import State
from .units import human

SYS_SERIES = (("disk", "Disk used"), ("ram", "RAM available"), ("psi", "Memory pressure (PSI some avg60)"))


def _journal(run, args: list[str]) -> list[str]:
    try:
        p = run(["journalctl", "--no-pager", "-o", "short", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in (p.stdout or "").splitlines() if line.strip() and not line.startswith("--")]


def _hm(t: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t))


def build_report(state: State, now: float, hours: int = 24, run=subprocess.run) -> str:
    since = now - hours * 3600
    out = [f"# Guardian report — {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}",
           f"Window: last {hours} h", "", "## System"]
    sys = state.samples("sys", since)
    for key, label in SYS_SERIES:
        vals = [v for _, v in sys.get(key, [])]
        out.append(f"- {label}: min {min(vals):.1f}%, avg {sum(vals) / len(vals):.1f}%, max {max(vals):.1f}%"
                   if vals else f"- {label}: no samples")
    out.append(f"- Guardian runs recorded: {len(sys.get('disk', []))} (≈{hours * 30} if the laptop was on all day)")

    titles: dict[str, str] = {}
    out += ["", "## Findings raised"]
    findings = state.findings_since(since)
    for f, first, status in findings:
        titles[f.id] = f.title
        acts = ", ".join(a.name + (f":{a.target}" if a.target else "") for a in f.actions) or "no action"
        out.append(f"- [{f.severity.value}] {f.title} — {f.reason} (first seen {_hm(first)}, {status}; {acts})")
    if not findings:
        out.append("- none")

    out += ["", "## Your decisions"]
    decisions = state.decisions_since(since)
    out += [f"- {_hm(at)} {choice}: {titles.get(fid, fid)}" for fid, choice, at in decisions] or ["- none"]

    out += ["", "## Actions run"]
    hist = state.history_since(since)
    out += [f"- {_hm(at)} {'ok' if ok else 'FAILED'} {action} — {detail[:200]}"
            for at, _fid, action, ok, detail in hist] or ["- none"]

    stamp = f"@{int(since)}"
    kills = [ln for ln in _journal(run, ["-u", "earlyoom", "--since", stamp]) if "to process" in ln]
    errors = _journal(run, ["--user", "-u", "guardian.service", "-p", "err", "--since", stamp])
    out += ["", "## earlyoom kills"] + ([f"- {k}" for k in kills] or ["- earlyoom kills: none"])
    out += ["", "## Guardian errors"] + ([f"- {e}" for e in errors[-20:]] or ["- Guardian errors: none"])

    raw = state.meta_get("scan")
    if raw:
        scan = DeepScan.model_validate_json(raw)
        out += ["", "## Largest folders (last deep scan)"]
        out += [f"- {human(d.size_bytes)} {d.path}" for d in scan.dirs[:8]]
    return "\n".join(out) + "\n"
