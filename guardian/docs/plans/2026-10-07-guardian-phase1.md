# Guardian Phase 1 (Disk + Process Manager) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A systemd-timer-driven Python CLI that watches disk and processes every 2 minutes, proposes fixes via desktop notifications with buttons, and only acts on a click (plus one zero-risk emergency path at disk ≥95%).

**Architecture:** Short-lived `guardian run` (probes → pure rules → findings in SQLite) launches a detached `guardian ask <id>` systemd unit per new warn/critical finding; the asker blocks on `notify-send -A`, records the decision, and on "Do it" runs only actions from a closed registry. An LLM advisor (`claude -p --model haiku`) produces text-only suggestions shown in `guardian review`.

**Tech Stack:** Python 3.12 (system), psutil, pydantic v2, sqlite3 (stdlib), pytest; systemd user units; polkit JS rule + one fixed root helper script; notify-send 0.8.3.

**Spec:** `docs/specs/2026-10-07-guardian-design.md`

**Deviations from spec (deliberate, flag in review):**
1. `Finding.actions: list[ActionSpec]` instead of a single `action` — disk findings bundle several cleanups behind one "Do it".
2. Advisor trigger is disk-only (disk ≥85% with nothing rule-reclaimable, or the disk finding was skipped). The spec's "unclassified process hog" trigger was ambiguous and is dropped.
3. The disk-path integration test uses an injected disk probe through the full `run_once` pipeline instead of a loop-mount (loop-mount needs root).
4. "Skip" on the critical disk finding counts as an answer, so the emergency auto-clean does not fire for it.
5. Advisor subprocess timeout is 120 s (spec said 60 s) because `claude -p` startup includes plugin hooks.

## Revision 2 (2026-10-07) — overhead, kernel/RAM review, simplification

Measured on the target laptop: Python+psutil+pydantic start = 0.06 s / 20 MB; `process_iter` over 409 procs = 0.07 s;
`docker stats --no-stream` = **2.07 s** + daemon work. The repo code is the source of truth where it differs from
the task code below. Changes:

1. **Docker probe reads cgroup v2 files, not `docker stats`.** `docker ps --no-trunc` (0.03 s) for id→name, then
   `/sys/fs/cgroup/system.slice/docker-<id>.scope/{memory.current,cpu.stat}`. `ContainerInfo.cpu_pct` becomes
   `cpu_usec` (cumulative). Idle = CPU% computed from the counter delta against the newest sample ≥6 h old; a counter
   that went down (restart) counts as activity. `stop_container` re-checks by reading `usage_usec` twice 2 s apart.
2. **RAM rule uses kernel PSI** (`/proc/pressure/memory`): warn if available <20% **or** `some avg60` ≥10%;
   critical if available <10% **or** `full avg60` ≥5%. Catches zram thrash that "% available" misses. Removes the
   "falling over 3 samples" heuristic and the `avail` sample series.
3. **Guardian bounds itself:** `guardian.service` gets `MemoryMax=600M` (covers the rare `claude -p` child);
   asker units get `-p MemoryMax=100M -p RuntimeMaxSec=3600`, so waiting askers can't pile up. The advisor is skipped
   when RAM available <30% (`claude -p` is ~400 MB of node).
4. **Cut:** the `reclaimable` info rule (disk <80% needs no nagging) and the `trash` cache entry (that's user data).
5. **Stress tests** (`tests/test_stress.py`, marker `stress`): real cgroup-limited memory hog → real PSI reading;
   per-run overhead budget with real probes (<0.5 s, <80 MB peak); 300 extra processes; 24 h of history;
   8 parallel processes writing state.

## Global Constraints

- Project root: `/home/arjun/guardian`; venv at `/home/arjun/guardian/.venv` created with `python3 -m venv`. All commands below run from the project root.
- Runtime deps exactly: `psutil>=5.9`, `pydantic>=2.6`. Dev dep: `pytest>=8`. No others.
- State DB: `$XDG_STATE_HOME/guardian/state.db`, default `~/.local/state/guardian/state.db`.
- Root helper path: `/usr/local/sbin/guardian-root-helper`; verbs exactly `apt-clean`, `journal-vacuum`, `rotated-logs`.
- LLM call: `claude -p --model haiku --output-format json`, prompt on stdin, directory names/sizes only — never file contents.
- Thresholds are module constants in `guardian/rules.py` (no config file).
- Cadence: timer every 2 min; deep scan every 6 h or when disk ≥85% (min gap 30 min); advisor max once / 6 h; re-ask after 2 h; "Skip" snoozes 24 h; emergency at most once / 30 min; info digest once / 24 h.
- No action outside `guardian/actions.py` REGISTRY may mutate the system.
- Git: commit steps run only once Arjun has OK'd committing; commit messages must NOT contain `Co-Authored-By` trailers.

## Review Focus

1. Docker not installed or daemon stopped → probes return empty data, the run still completes (Task 3 tests).
2. `claude` missing, logged out, timing out, or replying in prose → fallback "largest folders" advice, no crash (Task 7 tests).
3. Notification daemon unavailable / `notify-send` exits non-zero → decision recorded as `timeout`, nothing executed (Task 9 test).
4. Process exits or its PID is reused between the scan and the click → kill refused with a clear message, no exception (Task 6 tests).
5. The asker unit and the timer run writing `state.db` at the same moment → no "database is locked" (Task 2 test).

---

## File Structure

```
guardian/
  __init__.py
  config.py        # paths & fixed maps: HELPER, GUARDIAN_BIN, CACHE_PATHS, state_db()
  units.py         # parse_size(), human()
  models.py        # all Pydantic models + finding_id()
  state.py         # SQLite State class
  fsutil.py        # dir_size()
  protect.py       # DEFAULT_PROTECTED, is_protected()
  probes/__init__.py
  probes/ram.py    # probe_ram()
  probes/disk.py   # probe_disk()
  probes/docker.py # probe_containers()
  probes/scan.py   # deep_scan() and helpers
  rules.py         # thresholds, ZERO_RISK, proc_key(), bundle_actions(), evaluate()
  actions.py       # ActionContext, REGISTRY, execute()
  advisor.py       # build_prompt(), parse_suggestions(), advise()
  ask.py           # notify_cmd(), parse_choice(), notify_plain(), launch_asker(), open_review()
  flows.py         # Deps, run_once(), ask_flow(), act_flow(), review_flow()
  cli.py           # argparse entrypoint main()
root-helper/guardian-root-helper
root-helper/50-guardian.rules
systemd/guardian.service
systemd/guardian.timer
systemd/guardian-env.desktop
install.sh
uninstall.sh
tests/__init__.py
tests/helpers.py   # Recorder, Clock, Harness
tests/test_*.py
```

---

### Task 1: Scaffold, units, models, config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `guardian/__init__.py`, `guardian/config.py`, `guardian/units.py`, `guardian/models.py`, `tests/__init__.py`
- Test: `tests/test_units.py`, `tests/test_models.py`

**Interfaces:**
- Produces: `parse_size(text: str) -> int`, `human(n: int) -> str`; `config.HELPER: str`, `config.GUARDIAN_BIN: Path`, `config.CACHE_PATHS: dict[str, str]`, `config.state_db() -> Path`; models `Severity, ProcInfo, RamFacts(.avail_pct), DiskFacts(.pct), ContainerInfo, Reclaimable, DirSize, DeepScan, Facts, History, ActionSpec, Finding, ActionResult, AdvisorSuggestion, Advice`, `finding_id(kind: str, key: str, severity: Severity) -> str`.

- [ ] **Step 1: Create scaffold files**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "guardian"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["psutil>=5.9", "pydantic>=2.6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
guardian = "guardian.cli:main"

[tool.setuptools.packages.find]
include = ["guardian*"]

[tool.pytest.ini_options]
markers = ["integration: touches docker / real processes"]
addopts = "-m 'not integration'"
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
```

`guardian/__init__.py` and `tests/__init__.py`: empty files.

`guardian/config.py`:
```python
"""Fixed paths and maps. Nothing here is user-configurable in Phase 1."""
import os
import sys
from pathlib import Path

HELPER = "/usr/local/sbin/guardian-root-helper"
# Console script installed next to the venv interpreter (not resolved: .venv/bin/python is a symlink).
GUARDIAN_BIN = Path(sys.executable).with_name("guardian")

# The only directories clear_user_cache may delete, relative to $HOME.
CACHE_PATHS: dict[str, str] = {
    "pip": ".cache/pip",
    "uv": ".cache/uv",
    "npm": ".npm/_cacache",
    "ms-playwright": ".cache/ms-playwright",
    "thumbnails": ".cache/thumbnails",
    "trash": ".local/share/Trash",
}


def state_db() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "guardian" / "state.db"
```

- [ ] **Step 2: Create venv and install**

Run: `python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]'`
Expected: exits 0 (the package has no modules yet besides config; that's fine).

- [ ] **Step 3: Write failing tests**

`tests/test_units.py`:
```python
import pytest

from guardian.units import human, parse_size


def test_parse_decimal_and_binary_units():
    assert parse_size("5.69GB") == 5_690_000_000
    assert parse_size("0B") == 0
    assert parse_size("17.48MiB") == round(17.48 * 2**20)
    assert parse_size("1.5 GiB") == round(1.5 * 2**30)


def test_parse_journalctl_single_letter_suffix_is_binary():
    assert parse_size("651.6M") == round(651.6 * 2**20)


@pytest.mark.parametrize("bad", ["--", "", "12XB", "GB"])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_size(bad)


def test_human():
    assert human(512) == "512 B"
    assert human(4_100_000_000) == "4.1 GB"
    assert human(2_000_000_000_000) == "2.0 TB"
```

`tests/test_models.py`:
```python
import pytest
from pydantic import ValidationError

from guardian.models import AdvisorSuggestion, DiskFacts, RamFacts, Severity, finding_id


def test_finding_id_is_stable_and_severity_specific():
    a = finding_id("disk_high", "/", Severity.warn)
    assert a == finding_id("disk_high", "/", Severity.warn)
    assert a != finding_id("disk_high", "/", Severity.critical)
    assert len(a) == 12 and all(c in "0123456789abcdef" for c in a)


def test_percentages():
    assert DiskFacts(mount="/", used=90, avail=10).pct == 90.0
    assert RamFacts(total=100, available=15, swap_total=0, swap_free=0).avail_pct == 15.0


def test_suggestion_confidence_bounded():
    with pytest.raises(ValidationError):
        AdvisorSuggestion(path="/x", size_bytes=1, what_it_is="", safe_to_delete_confidence=1.5, how_to_clean="")
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_units.py tests/test_models.py -v`
Expected: FAIL / errors with `ModuleNotFoundError: No module named 'guardian.units'`.

- [ ] **Step 5: Implement**

`guardian/units.py`:
```python
import re

_UNITS = {
    "b": 1,
    "kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12,
    "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40,
}
_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")


def parse_size(text: str) -> int:
    """Parse docker/journalctl sizes ('5.69GB', '243.6MiB', '651.6M') into bytes."""
    m = _RE.match(text)
    if not m:
        raise ValueError(f"bad size: {text!r}")
    num, unit = m.groups()
    unit = (unit or "b").lower()
    if unit in ("k", "m", "g", "t"):  # journalctl prints binary units with one letter
        unit += "ib"
    if unit not in _UNITS:
        raise ValueError(f"bad size unit: {text!r}")
    return round(float(num) * _UNITS[unit])


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"
```

`guardian/models.py`:
```python
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
    id: str
    name: str
    cpu_pct: float
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
    avail: list[tuple[float, float]] = Field(default_factory=list)           # [(t, MemAvailable)]
    cpu: dict[str, list[tuple[float, float]]] = Field(default_factory=dict)  # container id -> [(t, cpu%)]


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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_units.py tests/test_models.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore guardian tests docs
git commit -m "feat: scaffold guardian package with units, models, config"
```

---

### Task 2: SQLite state

**Files:**
- Create: `guardian/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `Finding` (Task 1).
- Produces: `State(path: Path)` with methods: `add_sample(kind, key, at, value)`, `samples(kind, since) -> dict[str, list[tuple[float, float]]]`, `prune_samples(before)`, `upsert_finding(f, now)`, `resolve_missing(current_ids: set[str])`, `get_finding(fid) -> Finding | None`, `first_seen(fid) -> float | None`, `should_ask(fid, now) -> bool`, `mark_asked(fid, now)`, `mark_done(fid)`, `record_decision(fid, choice, now)`, `last_decision(fid, since=0.0) -> str | None`, `unanswered_since(fid) -> float | None`, `record_history(fid, action, ok, detail, now)`, `history(limit=50) -> list[tuple]`, `pending() -> list[Finding]`, `meta_get(key) -> str | None`, `meta_set(key, value)`, `protected() -> set[str]`, `add_protected(name)`. Constants `REASK_SECONDS = 7200`, `SNOOZE_SECONDS = 86400`.

- [ ] **Step 1: Write failing tests**

`tests/test_state.py`:
```python
import threading

import pytest

from guardian.models import ActionSpec, Finding, Severity
from guardian.state import State


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "s.db")


def mk(fid="f1", sev=Severity.warn):
    return Finding(id=fid, kind="disk_high", severity=sev, title="t", reason="r")


def test_new_finding_asked_once_then_waits_2h(state):
    state.upsert_finding(mk(), now=1000)
    assert state.should_ask("f1", 1000)
    state.mark_asked("f1", 1000)
    assert not state.should_ask("f1", 1000 + 7199)
    assert state.should_ask("f1", 1000 + 7200)


def test_skip_snoozes_24h(state):
    state.upsert_finding(mk(), 1000)
    state.mark_asked("f1", 1000)
    state.record_decision("f1", "skip", 1000)
    assert not state.should_ask("f1", 1000 + 86399)
    assert state.should_ask("f1", 1000 + 86400)
    assert state.last_decision("f1") == "skip"


def test_resolved_finding_rearms_when_it_returns(state):
    state.upsert_finding(mk(), 1000)
    state.mark_asked("f1", 1000)
    state.resolve_missing(set())
    assert not state.should_ask("f1", 1001)
    state.upsert_finding(mk(), 2000)
    assert state.should_ask("f1", 2000)
    assert state.first_seen("f1") == 2000


def test_done_finding_reasks_after_2h_if_condition_persists(state):
    state.upsert_finding(mk(), 1000)
    state.mark_asked("f1", 1000)
    state.mark_done("f1")
    state.upsert_finding(mk(), 1100)
    assert not state.should_ask("f1", 1100)
    assert state.should_ask("f1", 1000 + 7200)


def test_unanswered_since(state):
    state.upsert_finding(mk("c", Severity.critical), 1000)
    assert state.unanswered_since("c") == 1000
    state.record_decision("c", "timeout", 1100)
    assert state.unanswered_since("c") == 1000
    state.record_decision("c", "skip", 1200)
    assert state.unanswered_since("c") is None
    assert state.unanswered_since("missing") is None


def test_old_decisions_do_not_count_after_finding_returns(state):
    state.upsert_finding(mk("c", Severity.critical), 1000)
    state.record_decision("c", "do", 1100)
    state.resolve_missing(set())
    state.upsert_finding(mk("c", Severity.critical), 5000)
    assert state.unanswered_since("c") == 5000


def test_samples_grouped_and_pruned(state):
    state.add_sample("rss", "k1", 1, 10)
    state.add_sample("rss", "k1", 2, 20)
    state.add_sample("rss", "k2", 2, 5)
    assert state.samples("rss", 0) == {"k1": [(1, 10), (2, 20)], "k2": [(2, 5)]}
    state.prune_samples(2)
    assert state.samples("rss", 0) == {"k1": [(2, 20)], "k2": [(2, 5)]}


def test_finding_roundtrip_and_pending(state):
    f = mk()
    f.actions = [ActionSpec(name="docker_builder_prune")]
    state.upsert_finding(f, 1)
    assert state.get_finding("f1") == f
    assert state.pending() == [f]
    state.mark_done("f1")
    assert state.pending() == []


def test_meta_protected_history(state):
    assert state.meta_get("x") is None
    state.meta_set("x", "1")
    state.meta_set("x", "2")
    assert state.meta_get("x") == "2"
    state.add_protected("node")
    state.add_protected("node")
    assert state.protected() == {"node"}
    state.record_history("f1", "apt_clean", True, "ok", 5)
    assert state.history() == [(5, "f1", "apt_clean", 1, "ok")]


def test_concurrent_writers_do_not_lock(tmp_path):
    path = tmp_path / "s.db"
    State(path)  # create schema first
    errors = []

    def writer(key):
        try:
            s = State(path)
            for i in range(200):
                s.add_sample("rss", key, i, i)
        except Exception as e:  # noqa: BLE001 - surface any lock error
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    got = State(path).samples("rss", 0)
    assert len(got["a"]) == 200 and len(got["b"]) == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.state'`.

- [ ] **Step 3: Implement**

`guardian/state.py`:
```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Finding

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings(
  id TEXT PRIMARY KEY, json TEXT NOT NULL, first_seen REAL NOT NULL,
  last_asked REAL, snoozed_until REAL, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS decisions(finding_id TEXT, choice TEXT, at REAL);
CREATE TABLE IF NOT EXISTS history(at REAL, finding_id TEXT, action TEXT, ok INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS samples(kind TEXT, key TEXT, at REAL, value REAL);
CREATE INDEX IF NOT EXISTS samples_idx ON samples(kind, at);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS protected(name TEXT PRIMARY KEY);
"""

REASK_SECONDS = 2 * 3600
SNOOZE_SECONDS = 24 * 3600


class State:
    """Finding lifecycle: pending -> (done | resolved) -> pending again if the condition returns."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def _one(self, sql: str, *args):
        return self.db.execute(sql, args).fetchone()

    # --- samples -------------------------------------------------------
    def add_sample(self, kind: str, key: str, at: float, value: float) -> None:
        self.db.execute("INSERT INTO samples VALUES (?,?,?,?)", (kind, key, at, value))

    def samples(self, kind: str, since: float) -> dict[str, list[tuple[float, float]]]:
        out: dict[str, list[tuple[float, float]]] = {}
        rows = self.db.execute(
            "SELECT key, at, value FROM samples WHERE kind=? AND at>=? ORDER BY at, rowid", (kind, since))
        for key, at, value in rows:
            out.setdefault(key, []).append((at, value))
        return out

    def prune_samples(self, before: float) -> None:
        self.db.execute("DELETE FROM samples WHERE at < ?", (before,))

    # --- findings ------------------------------------------------------
    def upsert_finding(self, f: Finding, now: float) -> None:
        row = self._one("SELECT status FROM findings WHERE id=?", f.id)
        js = f.model_dump_json()
        if row is None:
            self.db.execute(
                "INSERT INTO findings(id, json, first_seen, status) VALUES (?,?,?,'pending')", (f.id, js, now))
        elif row[0] == "resolved":
            self.db.execute(
                "UPDATE findings SET json=?, first_seen=?, last_asked=NULL, status='pending' WHERE id=?",
                (js, now, f.id))
        else:  # pending or done: keep first_seen/last_asked so re-ask timing holds
            self.db.execute("UPDATE findings SET json=?, status='pending' WHERE id=?", (js, f.id))

    def resolve_missing(self, current_ids: set[str]) -> None:
        for (fid,) in self.db.execute("SELECT id FROM findings WHERE status='pending'").fetchall():
            if fid not in current_ids:
                self.db.execute("UPDATE findings SET status='resolved' WHERE id=?", (fid,))

    def get_finding(self, fid: str) -> Finding | None:
        row = self._one("SELECT json FROM findings WHERE id=?", fid)
        return Finding.model_validate_json(row[0]) if row else None

    def first_seen(self, fid: str) -> float | None:
        row = self._one("SELECT first_seen FROM findings WHERE id=?", fid)
        return row[0] if row else None

    def should_ask(self, fid: str, now: float) -> bool:
        row = self._one("SELECT last_asked, snoozed_until, status FROM findings WHERE id=?", fid)
        if row is None or row[2] != "pending":
            return False
        last_asked, snoozed_until, _ = row
        if snoozed_until is not None and now < snoozed_until:
            return False
        return last_asked is None or now - last_asked >= REASK_SECONDS

    def mark_asked(self, fid: str, now: float) -> None:
        self.db.execute("UPDATE findings SET last_asked=? WHERE id=?", (now, fid))

    def mark_done(self, fid: str) -> None:
        self.db.execute("UPDATE findings SET status='done' WHERE id=?", (fid,))

    def pending(self) -> list[Finding]:
        rows = self.db.execute("SELECT json FROM findings WHERE status='pending' ORDER BY first_seen")
        return [Finding.model_validate_json(r[0]) for r in rows]

    # --- decisions & history -------------------------------------------
    def record_decision(self, fid: str, choice: str, now: float) -> None:
        self.db.execute("INSERT INTO decisions VALUES (?,?,?)", (fid, choice, now))
        if choice == "skip":
            self.db.execute("UPDATE findings SET snoozed_until=? WHERE id=?", (now + SNOOZE_SECONDS, fid))

    def last_decision(self, fid: str, since: float = 0.0) -> str | None:
        row = self._one(
            "SELECT choice FROM decisions WHERE finding_id=? AND at>=? ORDER BY at DESC, rowid DESC LIMIT 1",
            fid, since)
        return row[0] if row else None

    def unanswered_since(self, fid: str) -> float | None:
        row = self._one("SELECT first_seen, status FROM findings WHERE id=?", fid)
        if row is None or row[1] != "pending":
            return None
        return row[0] if self.last_decision(fid, since=row[0]) in (None, "timeout") else None

    def record_history(self, fid: str, action: str, ok: bool, detail: str, now: float) -> None:
        self.db.execute("INSERT INTO history VALUES (?,?,?,?,?)", (now, fid, action, int(ok), detail))

    def history(self, limit: int = 50) -> list[tuple]:
        return self.db.execute(
            "SELECT at, finding_id, action, ok, detail FROM history ORDER BY at DESC, rowid DESC LIMIT ?",
            (limit,)).fetchall()

    # --- meta & protected ----------------------------------------------
    def meta_get(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key=?", key)
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def protected(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT name FROM protected")}

    def add_protected(self, name: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO protected VALUES (?)", (name,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/state.py tests/test_state.py
git commit -m "feat: sqlite state with finding lifecycle, samples, decisions"
```

---

### Task 3: Live probes (RAM, disk, Docker)

**Files:**
- Create: `guardian/probes/__init__.py` (empty), `guardian/probes/ram.py`, `guardian/probes/disk.py`, `guardian/probes/docker.py`, `tests/helpers.py`
- Test: `tests/test_probes.py`

**Interfaces:**
- Consumes: models, `parse_size`.
- Produces: `probe_ram(top_n: int = 15) -> RamFacts`; `probe_disk(mount: str = "/") -> DiskFacts`; `probe_containers(run=subprocess.run) -> list[ContainerInfo]`; test helper `Recorder(responses: dict[str, tuple[str, int]] | None = None, exc: Exception | None = None)` with `.calls`, `.kwargs`, `.ran(*prefix) -> bool` (responses match when the key is a substring of the joined command).

- [ ] **Step 1: Write the test helper**

`tests/helpers.py`:
```python
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
```

- [ ] **Step 2: Write failing tests**

`tests/test_probes.py`:
```python
import json
import os

from guardian.probes.disk import probe_disk
from guardian.probes.docker import probe_containers
from guardian.probes.ram import probe_ram
from tests.helpers import Recorder


def stats_line(**over):
    d = {"ID": "abc123def456", "Name": "memobase-api", "CPUPerc": "0.15%", "MemUsage": "243.6MiB / 11.48GiB"}
    d.update(over)
    return json.dumps(d)


def test_probe_containers_parses_docker_stats():
    [c] = probe_containers(Recorder({"docker stats": (stats_line() + "\n", 0)}))
    assert c.id == "abc123def456"
    assert c.name == "memobase-api"
    assert c.cpu_pct == 0.15
    assert c.mem_bytes == round(243.6 * 2**20)


def test_probe_containers_skips_unparseable_lines():
    out = "\n".join([stats_line(CPUPerc="--"), "not json", stats_line(ID="fff000fff000")])
    got = probe_containers(Recorder({"docker stats": (out, 0)}))
    assert [c.id for c in got] == ["fff000fff000"]


def test_probe_containers_docker_missing_returns_empty():
    assert probe_containers(Recorder(exc=FileNotFoundError("docker"))) == []


def test_probe_containers_daemon_down_returns_empty():
    assert probe_containers(Recorder({"docker stats": ("", 1)})) == []


def test_probe_disk_matches_df_formula(tmp_path):
    f = probe_disk(str(tmp_path))
    st = os.statvfs(tmp_path)
    assert f.mount == str(tmp_path)
    assert f.avail == st.f_bavail * st.f_frsize
    assert f.used == (st.f_blocks - st.f_bfree) * st.f_frsize
    assert 0 < f.pct < 100


def test_probe_ram_real_system():
    r = probe_ram(top_n=5)
    assert r.total > 0 and 0 < r.available <= r.total
    assert len(r.top) <= 5
    rss = [p.rss for p in r.top]
    assert rss == sorted(rss, reverse=True)
    assert any(p.pid == os.getpid() for p in probe_ram(top_n=10_000).top)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_probes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.probes'`.

- [ ] **Step 4: Implement**

`guardian/probes/__init__.py`: empty.

`guardian/probes/ram.py`:
```python
import psutil

from ..models import ProcInfo, RamFacts


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
    return RamFacts(total=vm.total, available=vm.available, swap_total=sw.total, swap_free=sw.free,
                    top=procs[:top_n])
```

`guardian/probes/disk.py`:
```python
import os

from ..models import DiskFacts


def probe_disk(mount: str = "/") -> DiskFacts:
    st = os.statvfs(mount)
    return DiskFacts(mount=mount, used=(st.f_blocks - st.f_bfree) * st.f_frsize, avail=st.f_bavail * st.f_frsize)
```

`guardian/probes/docker.py`:
```python
import json
import subprocess

from ..models import ContainerInfo
from ..units import parse_size


def probe_containers(run=subprocess.run) -> list[ContainerInfo]:
    """Running containers with CPU% and memory. Docker missing or down -> []."""
    try:
        p = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if p.returncode != 0:
        return []
    out: list[ContainerInfo] = []
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
            out.append(ContainerInfo(
                id=d["ID"],
                name=d["Name"],
                cpu_pct=float(d["CPUPerc"].rstrip("%")),
                mem_bytes=parse_size(d["MemUsage"].split("/")[0]),
            ))
        except (ValueError, KeyError, AttributeError):
            continue
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_probes.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add guardian/probes tests/helpers.py tests/test_probes.py
git commit -m "feat: ram, disk and docker probes"
```

---

### Task 4: Deep disk scan

**Files:**
- Create: `guardian/fsutil.py`, `guardian/probes/scan.py`
- Test: `tests/test_scan.py`

**Interfaces:**
- Consumes: `CACHE_PATHS`, `parse_size`, models `DeepScan, DirSize, Reclaimable`.
- Produces: `dir_size(path: Path) -> int` (never follows symlinks); `deep_scan(home: Path, run=subprocess.run, now: float | None = None, apt_dir: Path = Path("/var/cache/apt/archives"), log_dir: Path = Path("/var/log")) -> DeepScan`; helpers `docker_build_cache(run) -> int`, `dangling_images(run) -> int`, `journal_size(run) -> int`, `rotated_logs(log_dir, now) -> int`, `top_dirs(home, run, limit=30) -> list[DirSize]`.

- [ ] **Step 1: Write failing tests**

`tests/test_scan.py`:
```python
import json
import os

from guardian.fsutil import dir_size
from guardian.probes.scan import (deep_scan, docker_build_cache, dangling_images, journal_size,
                                  rotated_logs, top_dirs)
from tests.helpers import Recorder


def test_dir_size_counts_files_and_ignores_dir_symlinks(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"y" * 50)
    (tmp_path / "link").symlink_to("/usr")
    assert dir_size(tmp_path) == 150


def test_dir_size_missing_path_is_zero(tmp_path):
    assert dir_size(tmp_path / "nope") == 0


def test_docker_build_cache_reads_build_cache_row():
    out = "\n".join([json.dumps({"Type": "Images", "Reclaimable": "6.82GB (50%)"}),
                     json.dumps({"Type": "Build Cache", "Reclaimable": "5.69GB"})])
    assert docker_build_cache(Recorder({"system df": (out, 0)})) == 5_690_000_000


def test_docker_helpers_survive_missing_docker():
    rec = Recorder(exc=FileNotFoundError("docker"))
    assert docker_build_cache(rec) == 0
    assert dangling_images(rec) == 0


def test_dangling_images_counts_ids():
    assert dangling_images(Recorder({"dangling": ("aaa\nbbb\n\n", 0)})) == 2


def test_journal_size_parses_binary_suffix():
    out = "Archived and active journals take up 651.6M in the file system.\n"
    assert journal_size(Recorder({"journalctl": (out, 0)})) == round(651.6 * 2**20)
    assert journal_size(Recorder({"journalctl": ("weird", 0)})) == 0


def test_rotated_logs_counts_only_old_rotated_files(tmp_path):
    now = 1_000_000_000.0
    for name in ("old.gz", "new.gz", "old.log", "syslog.1"):
        (tmp_path / name).write_bytes(b"z" * 10)
    old = now - 8 * 86400
    for name in ("old.gz", "old.log", "syslog.1"):
        os.utime(tmp_path / name, (old, old))
    os.utime(tmp_path / "new.gz", (now, now))
    assert rotated_logs(tmp_path, now) == 20  # old.gz + syslog.1


def test_top_dirs_parses_du_and_skips_home(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    out = f"300\t{tmp_path/'a'}\n200\t{tmp_path/'b'}\n1000\t{tmp_path}\ngarbage\n"
    got = top_dirs(tmp_path, Recorder({"du ": (out, 1)}))  # du exits 1 on permission errors
    assert [(d.path, d.size_bytes) for d in got] == [(str(tmp_path / "a"), 300), (str(tmp_path / "b"), 200)]


def test_deep_scan_collects_user_caches(tmp_path):
    pip = tmp_path / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "f").write_bytes(b"x" * 10)
    scan = deep_scan(tmp_path, run=Recorder(), now=5.0, apt_dir=tmp_path / "apt", log_dir=tmp_path / "log")
    assert scan.at == 5.0
    assert scan.reclaim.user_caches == {"pip": 10}
    assert scan.reclaim.build_cache == 0 and scan.reclaim.apt_cache == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.fsutil'`.

- [ ] **Step 3: Implement**

`guardian/fsutil.py`:
```python
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
```

`guardian/probes/scan.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scan.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/fsutil.py guardian/probes/scan.py tests/test_scan.py
git commit -m "feat: deep disk scan of reclaimable caches and largest dirs"
```

---

### Task 5: Protection list and rules

**Files:**
- Create: `guardian/protect.py`, `guardian/rules.py`
- Test: `tests/test_protect.py`, `tests/test_rules.py`

**Interfaces:**
- Consumes: models, `human`, `GUARDIAN_BIN`.
- Produces: `DEFAULT_PROTECTED: tuple[str, ...]`, `is_protected(name: str, cmdline: str, extra: set[str]) -> bool`; rules constants (`DISK_INFO=80, DISK_WARN=85, DISK_CRIT=92, DISK_EMERG=95, EMERGENCY_WAIT=600, RAM_WARN=20, RAM_CRIT=10, LEAK_BYTES=1.5e9, LEAK_WINDOW=1800, IDLE_CPU=1.0, IDLE_WINDOW=21600, IDLE_MIN_MEM=1e8`), `ZERO_RISK: tuple[ActionSpec, ...]`, `proc_key(p: ProcInfo) -> str`, `bundle_actions(r: Reclaimable) -> tuple[list[ActionSpec], int]`, `evaluate(facts: Facts, hist: History, protected: set[str], critical_unanswered_since: float | None, my_uid: int | None = None) -> list[Finding]`. Finding kinds: `disk_high`, `disk_emergency`, `ram_low`, `leak_suspect`, `container_idle`, `reclaimable`. Finding id keys: disk → mount; ram → `"mem"`; leak → `proc_key`; container → container id; reclaimable → `"all"`.

- [ ] **Step 1: Write failing tests**

`tests/test_protect.py`:
```python
from guardian.config import GUARDIAN_BIN
from guardian.protect import is_protected


def test_default_patterns():
    for name in ("cinnamon", "cinnamon-session", "Xorg", "gdm-x-session", "systemd", "sshd",
                 "pipewire-pulse", "tailscaled", "earlyoom", "claude"):
        assert is_protected(name, "", set()), name
    assert not is_protected("python3", "python3 app.py", set())


def test_extra_patterns_and_self():
    assert is_protected("node", "", {"node"})
    assert is_protected("python3", f"/x/python {GUARDIAN_BIN} run", set())
```

`tests/test_rules.py`:
```python
from guardian.models import (ContainerInfo, DeepScan, DiskFacts, Facts, History, ProcInfo, RamFacts,
                             Reclaimable, Severity)
from guardian.rules import ZERO_RISK, evaluate

GB = 10**9
NOW = 100_000.0


def facts(disk_pct=50.0, avail_pct=50.0, top=(), containers=(), reclaim=None):
    used = int(100 * GB * disk_pct / 100)
    return Facts(
        now=NOW,
        ram=RamFacts(total=10 * GB, available=int(10 * GB * avail_pct / 100), swap_total=0, swap_free=0,
                     top=list(top)),
        disk=DiskFacts(mount="/", used=used, avail=100 * GB - used),
        containers=list(containers),
        scan=DeepScan(at=NOW, reclaim=reclaim) if reclaim is not None else None,
    )


def proc(pid=500, rss=GB, name="python3", uid=1000, start=1.0):
    return ProcInfo(pid=pid, start_time=start, name=name, cmdline=f"{name} app.py", rss=rss, uid=uid)


def ev(f, hist=None, since=None):
    return evaluate(f, hist or History(), protected=set(), critical_unanswered_since=since, my_uid=1000)


def of(findings, kind):
    return [x for x in findings if x.kind == kind]


# --- disk ---------------------------------------------------------------
def test_disk_below_80_is_quiet():
    assert of(ev(facts(disk_pct=79)), "disk_high") == []


def test_disk_80_is_info():
    [d] = of(ev(facts(disk_pct=81)), "disk_high")
    assert d.severity is Severity.info


def test_disk_86_warns_with_bundled_reclaim_actions():
    r = Reclaimable(build_cache=3 * GB, user_caches={"pip": 200_000_000, "uv": 50_000_000})
    [d] = of(ev(facts(disk_pct=86, reclaim=r)), "disk_high")
    assert d.severity is Severity.warn
    assert [(a.name, a.target) for a in d.actions] == [("docker_builder_prune", None), ("clear_user_cache", "pip")]
    assert d.est_saving_bytes == 3 * GB + 200_000_000


def test_disk_high_without_scan_points_to_review():
    [d] = of(ev(facts(disk_pct=86)), "disk_high")
    assert d.actions == [] and "guardian review" in d.reason


def test_disk_93_is_critical():
    [d] = of(ev(facts(disk_pct=93)), "disk_high")
    assert d.severity is Severity.critical


def test_emergency_after_10_unanswered_minutes():
    out = ev(facts(disk_pct=96), since=NOW - 600)
    [e] = of(out, "disk_emergency")
    assert e.severity is Severity.emergency
    assert [(a.name, a.target) for a in e.actions] == [(a.name, a.target) for a in ZERO_RISK]
    assert of(out, "disk_high")[0].severity is Severity.critical  # critical stays alongside


def test_no_emergency_before_10_minutes_or_when_answered():
    assert of(ev(facts(disk_pct=96), since=NOW - 599), "disk_emergency") == []
    assert of(ev(facts(disk_pct=96), since=None), "disk_emergency") == []


def test_zero_risk_list_contains_no_kill_or_stop():
    assert {a.name for a in ZERO_RISK} == {"docker_builder_prune", "clear_user_cache", "apt_clean",
                                          "journal_vacuum", "rotated_logs_clean"}
    assert {a.target for a in ZERO_RISK if a.name == "clear_user_cache"} == {"pip", "uv", "npm"}


# --- ram ----------------------------------------------------------------
def test_ram_warn_targets_biggest_own_unprotected_process():
    top = [proc(pid=1, name="cinnamon", rss=3 * GB), proc(pid=2, uid=0, rss=2 * GB), proc(pid=3, rss=GB)]
    [r] = of(ev(facts(avail_pct=15, top=top)), "ram_low")
    assert r.severity is Severity.warn
    [a] = r.actions
    assert a.name == "kill_process" and a.target == "3"
    assert a.fingerprint == {"pid": 3, "start_time": 1.0, "name": "python3"}


def test_ram_critical_only_when_falling():
    falling = History(avail=[(NOW - 240, 1.0 * GB), (NOW - 120, 0.9 * GB)])
    rising = History(avail=[(NOW - 240, 0.7 * GB), (NOW - 120, 0.75 * GB)])
    assert of(ev(facts(avail_pct=8, top=[proc()]), falling), "ram_low")[0].severity is Severity.critical
    assert of(ev(facts(avail_pct=8, top=[proc()]), rising), "ram_low")[0].severity is Severity.warn


def test_ram_low_without_candidates_has_no_action():
    [r] = of(ev(facts(avail_pct=15, top=[proc(name="Xorg")])), "ram_low")
    assert r.actions == []


# --- leaks --------------------------------------------------------------
def test_leak_detected_when_growth_over_threshold_and_still_growing():
    hist = History(rss={"500:1.0": [(NOW - 1700, 0.5 * GB), (NOW - 600, 1.5 * GB)]})
    [l] = of(ev(facts(top=[proc(rss=int(2.1 * GB))]), hist), "leak_suspect")
    assert l.actions[0].name == "kill_process"


def test_no_leak_when_not_still_growing():
    hist = History(rss={"500:1.0": [(NOW - 1700, 0.5 * GB), (NOW - 600, 2.2 * GB)]})
    assert of(ev(facts(top=[proc(rss=int(2.1 * GB))]), hist), "leak_suspect") == []


def test_samples_outside_window_ignored():
    hist = History(rss={"500:1.0": [(NOW - 2000, 0.1 * GB), (NOW - 100, 1.0 * GB)]})
    assert of(ev(facts(top=[proc(rss=int(1.7 * GB))]), hist), "leak_suspect") == []


def test_protected_process_never_flagged_as_leak():
    hist = History(rss={"500:1.0": [(NOW - 1700, 0.1 * GB), (NOW - 600, 1.0 * GB)]})
    assert of(ev(facts(top=[proc(name="claude", rss=3 * GB)]), hist), "leak_suspect") == []


# --- idle containers ----------------------------------------------------
def c1(cpu=0.1, mem=300_000_000):
    return ContainerInfo(id="c1", name="memobase-api", cpu_pct=cpu, mem_bytes=mem)


IDLE_HIST = History(cpu={"c1": [(NOW - 6 * 3600 - 60, 0.1), (NOW - 3600, 0.2)]})


def test_idle_container_is_info_with_stop_action():
    [c] = of(ev(facts(containers=[c1()]), IDLE_HIST), "container_idle")
    assert c.severity is Severity.info
    assert (c.actions[0].name, c.actions[0].target) == ("stop_container", "c1")


def test_idle_container_warns_when_ram_low():
    [c] = of(ev(facts(avail_pct=15, containers=[c1()]), IDLE_HIST), "container_idle")
    assert c.severity is Severity.warn


def test_busy_short_history_or_small_container_not_idle():
    busy = History(cpu={"c1": [(NOW - 6 * 3600 - 60, 0.1), (NOW - 3600, 5.0)]})
    short = History(cpu={"c1": [(NOW - 3600, 0.1)]})
    assert of(ev(facts(containers=[c1()]), busy), "container_idle") == []
    assert of(ev(facts(containers=[c1()]), short), "container_idle") == []
    assert of(ev(facts(containers=[c1(mem=50_000_000)]), IDLE_HIST), "container_idle") == []


# --- reclaimable info ---------------------------------------------------
def test_reclaimable_info_when_disk_fine_and_caches_big():
    [r] = of(ev(facts(reclaim=Reclaimable(build_cache=3 * GB))), "reclaimable")
    assert r.severity is Severity.info and r.actions[0].name == "docker_builder_prune"
    assert of(ev(facts(reclaim=Reclaimable(build_cache=GB))), "reclaimable") == []


def test_reclaimable_not_duplicated_when_disk_high():
    out = ev(facts(disk_pct=86, reclaim=Reclaimable(build_cache=3 * GB)))
    assert of(out, "reclaimable") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_protect.py tests/test_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.protect'`.

- [ ] **Step 3: Implement**

`guardian/protect.py`:
```python
from fnmatch import fnmatch

from .config import GUARDIAN_BIN

DEFAULT_PROTECTED: tuple[str, ...] = (
    "cinnamon*", "Xorg", "gdm*", "systemd*", "sshd", "dbus-daemon", "pipewire*", "wireplumber",
    "tailscaled", "earlyoom", "claude", "guardian",
)


def is_protected(name: str, cmdline: str, extra: set[str]) -> bool:
    if any(fnmatch(name, pat) for pat in (*DEFAULT_PROTECTED, *extra)):
        return True
    return str(GUARDIAN_BIN) in cmdline  # never kill ourselves (python3 .venv/bin/guardian ...)
```

`guardian/rules.py`:
```python
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
RAM_WARN, RAM_CRIT = 20.0, 10.0
LEAK_BYTES, LEAK_WINDOW = 1.5 * GB, 1800
IDLE_CPU, IDLE_WINDOW, IDLE_MIN_MEM = 1.0, 6 * 3600, 100 * MB
BUNDLE_MIN = 100 * MB
JOURNAL_MIN, JOURNAL_KEEP = 500 * MB, 200 * MB
ROTATED_MIN = 50 * MB
INFO_BUILD_CACHE, INFO_USER_CACHE = 2 * GB, 1 * GB

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
        *_ram(facts, hist, protected, uid),
        *_leaks(facts, hist, protected, uid),
        *_idle(facts, hist),
        *_reclaim_info(facts),
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


def _ram(facts: Facts, hist: History, protected: set[str], uid: int) -> list[Finding]:
    r = facts.ram
    pct = r.avail_pct
    if pct >= RAM_WARN:
        return []
    recent = [v for _, v in hist.avail[-2:]] + [r.available]
    falling = len(recent) == 3 and recent[0] > recent[1] > recent[2]
    sev = Severity.critical if pct < RAM_CRIT and falling else Severity.warn
    head = f"Only {human(r.available)} RAM available ({pct:.0f}%)."
    cands = _killable(facts, protected, uid)
    if cands:
        hog = cands[0]
        reason, acts, est = (f"{head} Biggest: {hog.name} (pid {hog.pid}) using {human(hog.rss)}.",
                             [_kill_action(hog)], hog.rss)
    else:
        reason, acts, est = f"{head} No killable process of yours found.", [], 0
    return [Finding(id=finding_id("ram_low", "mem", sev), kind="ram_low", severity=sev,
                    title=f"Low RAM: {pct:.0f}% available", reason=reason, est_saving_bytes=est, actions=acts)]


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
        pts = hist.cpu.get(c.id, []) + [(facts.now, c.cpu_pct)]
        if pts[0][0] > facts.now - IDLE_WINDOW:
            continue  # not watched for 6 h yet
        if max(v for t, v in pts if t >= facts.now - IDLE_WINDOW) >= IDLE_CPU:
            continue
        sev = Severity.warn if low_ram else Severity.info
        out.append(Finding(
            id=finding_id("container_idle", c.id, sev), kind="container_idle", severity=sev,
            title=f"Idle container: {c.name}",
            reason=(f"{c.name} used <{IDLE_CPU:.0f}% CPU for 6+ h and holds {human(c.mem_bytes)} RAM. "
                    f"`docker start {c.name}` brings it back."),
            est_saving_bytes=c.mem_bytes,
            actions=[ActionSpec(name="stop_container", target=c.id, fingerprint={"name": c.name})],
        ))
    return out


def _reclaim_info(facts: Facts) -> list[Finding]:
    if facts.scan is None or facts.disk.pct >= DISK_INFO:
        return []  # when disk is high, reclaim is bundled into disk_high instead
    r = facts.scan.reclaim
    acts: list[ActionSpec] = []
    total = 0
    if r.build_cache >= INFO_BUILD_CACHE:
        acts.append(ActionSpec(name="docker_builder_prune"))
        total += r.build_cache
    for name, size in sorted(r.user_caches.items()):
        if size >= INFO_USER_CACHE:
            acts.append(ActionSpec(name="clear_user_cache", target=name))
            total += size
    if r.journal >= JOURNAL_MIN:
        acts.append(ActionSpec(name="journal_vacuum"))
        total += r.journal - JOURNAL_KEEP
    if not acts:
        return []
    return [Finding(
        id=finding_id("reclaimable", "all", Severity.info), kind="reclaimable", severity=Severity.info,
        title=f"{human(total)} of caches can be cleaned",
        reason="Safe-to-delete caches have grown: " + ", ".join(a.target or a.name for a in acts) + ".",
        est_saving_bytes=total, actions=acts,
    )]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_protect.py tests/test_rules.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/protect.py guardian/rules.py tests/test_protect.py tests/test_rules.py
git commit -m "feat: protection list and pure rules engine"
```

---

### Task 6: Action registry with safety guards

**Files:**
- Create: `guardian/actions.py`
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: `ActionSpec`, `ActionResult`, `CACHE_PATHS`, `HELPER`, `dir_size`, `is_protected`, `parse_size`.
- Produces: `ActionContext(home: Path = Path.home(), run = subprocess.run, protected: set[str] = set(), kill_grace: float = 10.0)` (dataclass); `REGISTRY: dict[str, Callable[[ActionSpec, ActionContext], ActionResult]]`; `execute(spec: ActionSpec, ctx: ActionContext) -> ActionResult` (never raises). Registry keys: `kill_process, stop_container, clear_user_cache, docker_builder_prune, docker_image_prune_dangling, apt_clean, journal_vacuum, rotated_logs_clean`.

- [ ] **Step 1: Write failing tests**

`tests/test_actions.py`:
```python
import subprocess
import sys

import psutil
import pytest

from guardian.actions import ActionContext, execute
from guardian.config import HELPER
from guardian.models import ActionSpec
from tests.helpers import Recorder


def ctx(home, rec=None, protected=None):
    return ActionContext(home=home, run=rec or Recorder(), protected=protected or set(), kill_grace=2)


@pytest.fixture
def child():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def fp(p):
    ps = psutil.Process(p.pid)
    return {"pid": p.pid, "start_time": ps.create_time(), "name": ps.name()}


def test_unknown_action_refused(tmp_path):
    r = execute(ActionSpec(name="rm_rf_home"), ctx(tmp_path))
    assert not r.ok and "unknown action" in r.detail


# --- clear_user_cache ---------------------------------------------------
def test_clear_user_cache_removes_only_the_mapped_dir(tmp_path):
    pip = tmp_path / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "x").write_bytes(b"x" * 100)
    other = tmp_path / ".cache" / "other"
    other.mkdir()
    (other / "y").write_bytes(b"y")
    r = execute(ActionSpec(name="clear_user_cache", target="pip"), ctx(tmp_path))
    assert r.ok and r.freed_bytes == 100
    assert not pip.exists() and (other / "y").exists()


@pytest.mark.parametrize("target", ["../..", "", None, "Documents"])
def test_clear_user_cache_unknown_name_refused(tmp_path, target):
    (tmp_path / "keep").write_text("k")
    r = execute(ActionSpec(name="clear_user_cache", target=target), ctx(tmp_path))
    assert not r.ok and "unknown cache" in r.detail
    assert (tmp_path / "keep").exists()


def test_clear_user_cache_symlink_refused(tmp_path):
    elsewhere = tmp_path / "precious"
    elsewhere.mkdir()
    (elsewhere / "thesis.tex").write_text("!")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "pip").symlink_to(elsewhere)
    r = execute(ActionSpec(name="clear_user_cache", target="pip"), ctx(tmp_path))
    assert not r.ok and "symlink" in r.detail
    assert (elsewhere / "thesis.tex").exists()


def test_clear_user_cache_missing_is_ok(tmp_path):
    r = execute(ActionSpec(name="clear_user_cache", target="uv"), ctx(tmp_path))
    assert r.ok and r.freed_bytes == 0


# --- kill_process -------------------------------------------------------
def test_kill_process_kills_matching_child(tmp_path, child):
    r = execute(ActionSpec(name="kill_process", target=str(child.pid), fingerprint=fp(child)), ctx(tmp_path))
    assert r.ok, r.detail
    assert child.wait(5) is not None


def test_kill_process_refuses_recycled_pid(tmp_path, child):
    f = fp(child)
    f["start_time"] += 100
    r = execute(ActionSpec(name="kill_process", fingerprint=f), ctx(tmp_path))
    assert not r.ok and "changed" in r.detail
    assert child.poll() is None


def test_kill_process_refuses_protected(tmp_path, child):
    f = fp(child)
    r = execute(ActionSpec(name="kill_process", fingerprint=f), ctx(tmp_path, protected={f["name"]}))
    assert not r.ok and "protected" in r.detail
    assert child.poll() is None


def test_kill_process_already_gone(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    f = fp(p)
    p.wait()
    r = execute(ActionSpec(name="kill_process", fingerprint=f), ctx(tmp_path))
    assert not r.ok and ("gone" in r.detail or "changed" in r.detail)


@pytest.mark.parametrize("bad", [{}, {"pid": "x", "start_time": 1, "name": "a"}, {"pid": 1, "start_time": 1, "name": "systemd"}])
def test_kill_process_bad_or_pid1_refused(tmp_path, bad):
    r = execute(ActionSpec(name="kill_process", fingerprint=bad), ctx(tmp_path))
    assert not r.ok


# --- stop_container -----------------------------------------------------
def test_stop_container_rejects_bad_id_without_running_anything(tmp_path):
    rec = Recorder()
    r = execute(ActionSpec(name="stop_container", target="abc; rm -rf /"), ctx(tmp_path, rec))
    assert not r.ok and rec.calls == []


def test_stop_container_refuses_when_busy(tmp_path):
    rec = Recorder({"docker stats": ("5.20%\n", 0)})
    r = execute(ActionSpec(name="stop_container", target="abc123def456"), ctx(tmp_path, rec))
    assert not r.ok and "busy" in r.detail
    assert not rec.ran("docker", "stop")


def test_stop_container_stops_when_idle(tmp_path):
    rec = Recorder({"docker stats": ("0.10%\n", 0)})
    r = execute(ActionSpec(name="stop_container", target="abc123def456"), ctx(tmp_path, rec))
    assert r.ok
    assert rec.ran("docker", "stop", "abc123def456")


# --- commands -----------------------------------------------------------
@pytest.mark.parametrize("name,verb", [("apt_clean", "apt-clean"), ("journal_vacuum", "journal-vacuum"),
                                       ("rotated_logs_clean", "rotated-logs")])
def test_root_actions_use_fixed_helper_verbs(tmp_path, name, verb):
    rec = Recorder()
    assert execute(ActionSpec(name=name, target="ignored; reboot"), ctx(tmp_path, rec)).ok
    assert rec.calls == [["pkexec", HELPER, verb]]


def test_builder_prune_reports_freed_bytes(tmp_path):
    rec = Recorder({"docker builder": ("Deleted ...\nTotal reclaimed space: 5.69GB\n", 0)})
    r = execute(ActionSpec(name="docker_builder_prune"), ctx(tmp_path, rec))
    assert r.ok and r.freed_bytes == 5_690_000_000
    assert rec.calls == [["docker", "builder", "prune", "-af"]]


def test_missing_command_returns_failure_not_exception(tmp_path):
    r = execute(ActionSpec(name="docker_builder_prune"), ctx(tmp_path, Recorder(exc=FileNotFoundError("docker"))))
    assert not r.ok and "failed" in r.detail


def test_nonzero_exit_is_failure(tmp_path):
    r = execute(ActionSpec(name="apt_clean"), ctx(tmp_path, Recorder({"pkexec": ("", 126)})))
    assert not r.ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.actions'`.

- [ ] **Step 3: Implement**

`guardian/actions.py`:
```python
"""The ONLY code allowed to change the system. Every entry re-validates its target before acting."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from .config import CACHE_PATHS, HELPER
from .fsutil import dir_size
from .models import ActionResult, ActionSpec
from .protect import is_protected
from .units import parse_size

CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
ROOT_VERBS = {"apt_clean": "apt-clean", "journal_vacuum": "journal-vacuum", "rotated_logs_clean": "rotated-logs"}
RECLAIMED = re.compile(r"Total reclaimed space:\s*([0-9.]+\s*[A-Za-z]+)")
IDLE_CPU = 1.0


@dataclass
class ActionContext:
    home: Path = field(default_factory=Path.home)
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    protected: set[str] = field(default_factory=set)
    kill_grace: float = 10.0


def _fail(detail: str) -> ActionResult:
    return ActionResult(ok=False, detail=detail)


def _cmd(ctx: ActionContext, args: list[str], timeout: int = 600) -> ActionResult:
    try:
        p = ctx.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _fail(f"{args[0]} failed: {e}")
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        return _fail(((p.stderr or "").strip() or out)[-300:] or f"{args[0]} exited {p.returncode}")
    return ActionResult(ok=True, detail=out[-300:])


def kill_process(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    try:
        pid = int(spec.fingerprint["pid"])
        start = float(spec.fingerprint["start_time"])
        name = str(spec.fingerprint["name"])
    except (KeyError, ValueError, TypeError):
        return _fail("refused: bad fingerprint")
    if pid <= 1 or pid == os.getpid():
        return _fail(f"refused: will not kill pid {pid}")
    try:
        p = psutil.Process(pid)
        if abs(p.create_time() - start) > 0.01 or p.name() != name:
            return _fail("refused: process changed since the scan (pid reused)")
        if is_protected(p.name(), " ".join(p.cmdline()), ctx.protected):
            return _fail(f"refused: {name} is protected")
        rss = p.memory_info().rss
        p.terminate()
        try:
            p.wait(ctx.kill_grace)
        except psutil.TimeoutExpired:
            p.kill()
            p.wait(5)
    except psutil.NoSuchProcess:
        return _fail("process already gone")
    except psutil.AccessDenied:
        return _fail(f"permission denied killing {name}")
    except psutil.TimeoutExpired:
        return _fail(f"{name} did not exit after SIGKILL")
    return ActionResult(ok=True, detail=f"killed {name} (pid {pid})", freed_bytes=rss)


def stop_container(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    cid = spec.target or ""
    if not CONTAINER_ID.match(cid):
        return _fail(f"refused: bad container id {cid!r}")
    r = _cmd(ctx, ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", cid], timeout=30)
    if not r.ok:
        return r
    try:
        cpu = float(r.detail.strip().rstrip("%"))
    except ValueError:
        return _fail(f"could not read CPU for {cid}: {r.detail!r}")
    if cpu >= IDLE_CPU:
        return _fail(f"refused: container busy ({cpu}% CPU)")
    return _cmd(ctx, ["docker", "stop", cid], timeout=60)


def clear_user_cache(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    rel = CACHE_PATHS.get(spec.target or "")
    if rel is None:
        return _fail(f"refused: unknown cache {spec.target!r}")
    path = ctx.home / rel
    if path.is_symlink():
        return _fail(f"refused: {path} is a symlink")
    if not path.exists():
        return ActionResult(ok=True, detail=f"{spec.target} cache already empty")
    size = dir_size(path)
    try:
        shutil.rmtree(path)
    except OSError as e:
        return _fail(f"could not clear {path}: {e}")
    return ActionResult(ok=True, detail=f"cleared {path}", freed_bytes=size)


def _docker_prune(args: list[str]):
    def act(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
        r = _cmd(ctx, args)
        m = RECLAIMED.search(r.detail)
        if r.ok and m:
            try:
                r.freed_bytes = parse_size(m.group(1).replace(" ", ""))
            except ValueError:
                pass
        return r
    return act


def _root(verb: str):
    return lambda spec, ctx: _cmd(ctx, ["pkexec", HELPER, verb])


REGISTRY: dict[str, Callable[[ActionSpec, ActionContext], ActionResult]] = {
    "kill_process": kill_process,
    "stop_container": stop_container,
    "clear_user_cache": clear_user_cache,
    "docker_builder_prune": _docker_prune(["docker", "builder", "prune", "-af"]),
    "docker_image_prune_dangling": _docker_prune(["docker", "image", "prune", "-f"]),
    **{name: _root(verb) for name, verb in ROOT_VERBS.items()},
}


def execute(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    fn = REGISTRY.get(spec.name)
    if fn is None:
        return _fail(f"refused: unknown action {spec.name!r}")
    try:
        return fn(spec, ctx)
    except Exception as e:  # noqa: BLE001 - an action must never crash the caller
        return _fail(f"error in {spec.name}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_actions.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/actions.py tests/test_actions.py
git commit -m "feat: closed action registry with fingerprint and path guards"
```

---

### Task 7: LLM advisor

**Files:**
- Create: `guardian/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Consumes: `DeepScan`, `AdvisorSuggestion`, `Advice`.
- Produces: `claude_bin() -> str`; `build_prompt(scan: DeepScan) -> str`; `parse_suggestions(raw: str) -> list[AdvisorSuggestion]` (raises `ValueError`); `advise(scan: DeepScan, run=subprocess.run, now: float | None = None) -> Advice` (never raises; `source` is `"llm"` or `"fallback"`). `ADVISOR_TIMEOUT = 120`.

- [ ] **Step 1: Write failing tests**

`tests/test_advisor.py`:
```python
import json
import subprocess

import pytest

from guardian.advisor import advise, build_prompt, parse_suggestions
from guardian.models import DeepScan, DirSize, Reclaimable
from tests.helpers import Recorder

SCAN = DeepScan(at=1, reclaim=Reclaimable(), dirs=[DirSize(path="/home/a/big", size_bytes=5 * 10**9, mtime=0)])
GOOD = json.dumps([{"path": "/home/a/big", "size_bytes": 5_000_000_000, "what_it_is": "old ISO",
                    "safe_to_delete_confidence": 0.8, "how_to_clean": "rm it"}])


def envelope(text):
    return json.dumps({"type": "result", "result": text})


def test_parse_envelope():
    [s] = parse_suggestions(envelope(GOOD))
    assert s.path == "/home/a/big" and s.safe_to_delete_confidence == 0.8


def test_parse_fenced_json():
    assert parse_suggestions(envelope("```json\n" + GOOD + "\n```"))[0].what_it_is == "old ISO"


@pytest.mark.parametrize("text", ['[{"path": "x"}]', "I think you should delete stuff", "[]x]"])
def test_parse_rejects_bad_output(text):
    with pytest.raises(ValueError):
        parse_suggestions(envelope(text))


def test_prompt_contains_paths_and_schema_only():
    p = build_prompt(SCAN)
    assert "/home/a/big" in p and "how_to_clean" in p


def test_advise_calls_haiku_with_prompt_on_stdin():
    rec = Recorder({"claude": (envelope(GOOD), 0)})
    a = advise(SCAN, run=rec, now=7)
    assert a.source == "llm" and a.at == 7 and len(a.suggestions) == 1
    cmd = rec.calls[0]
    assert cmd[1:] == ["-p", "--model", "haiku", "--output-format", "json"]
    assert "/home/a/big" in rec.kwargs[0]["input"]


@pytest.mark.parametrize("rec", [
    Recorder(exc=FileNotFoundError("claude")),
    Recorder(exc=subprocess.TimeoutExpired("claude", 120)),
    Recorder({"claude": ("", 1)}),
    Recorder({"claude": (envelope("sorry, I can't"), 0)}),
    Recorder({"claude": ("not json at all", 0)}),
])
def test_advise_falls_back_to_largest_dirs(rec):
    a = advise(SCAN, run=rec, now=7)
    assert a.source == "fallback" and a.suggestions == [] and a.top_dirs == SCAN.dirs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_advisor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.advisor'`.

- [ ] **Step 3: Implement**

`guardian/advisor.py`:
```python
"""Text-only disk advice from Claude Haiku. Output is never executed."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from pydantic import TypeAdapter

from .models import Advice, AdvisorSuggestion, DeepScan

ADVISOR_TIMEOUT = 120
_SUGGESTIONS = TypeAdapter(list[AdvisorSuggestion])

PROMPT = """You are a Linux disk-usage advisor for a developer laptop.
Below is JSON with the largest directories (path, size_bytes, mtime) and known reclaimable caches.
Return ONLY a JSON array (no prose, no markdown) of at most 8 objects with exactly these keys:
path, size_bytes, what_it_is, safe_to_delete_confidence (number 0..1), how_to_clean.
Never recommend deleting source code, git repositories, documents, databases or Docker volumes;
if unsure what something is, say so and use a confidence below 0.3.

DATA:
{data}
"""


def claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def build_prompt(scan: DeepScan) -> str:
    data = {"dirs": [d.model_dump() for d in scan.dirs[:30]], "reclaimable": scan.reclaim.model_dump()}
    return PROMPT.format(data=json.dumps(data, indent=1))


def parse_suggestions(raw: str) -> list[AdvisorSuggestion]:
    try:
        env = json.loads(raw)
        text = env["result"] if isinstance(env, dict) else raw
    except (json.JSONDecodeError, KeyError):
        text = raw
    if not isinstance(text, str):
        raise ValueError("advisor result is not text")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in advisor output")
    return _SUGGESTIONS.validate_python(json.loads(text[start:end + 1]))


def advise(scan: DeepScan, run=subprocess.run, now: float | None = None) -> Advice:
    now = time.time() if now is None else now
    try:
        p = run([claude_bin(), "-p", "--model", "haiku", "--output-format", "json"],
                input=build_prompt(scan), capture_output=True, text=True, timeout=ADVISOR_TIMEOUT)
        if p.returncode != 0:
            raise ValueError(f"claude exited {p.returncode}")
        return Advice(at=now, source="llm", suggestions=parse_suggestions(p.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return Advice(at=now, source="fallback", top_dirs=scan.dirs[:10])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_advisor.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/advisor.py tests/test_advisor.py
git commit -m "feat: haiku disk advisor with schema validation and fallback"
```

---

### Task 8: Notifications and asker launch

**Files:**
- Create: `guardian/ask.py`
- Test: `tests/test_ask.py`

**Interfaces:**
- Consumes: `Finding`, `Severity`, `human`, `GUARDIAN_BIN`.
- Produces: `notify_cmd(f: Finding) -> list[str]`; `parse_choice(stdout: str) -> str` (one of `do|skip|details|timeout`); `notify_plain(title: str, body: str, run=subprocess.run) -> None`; `launch_asker(fid: str, run=subprocess.run) -> None`; `open_review(spawn=subprocess.Popen) -> None`. None of the three side-effect functions raise on `OSError`/timeout except `open_review` (caller handles).

- [ ] **Step 1: Write failing tests**

`tests/test_ask.py`:
```python
import subprocess

from guardian.ask import launch_asker, notify_cmd, notify_plain, parse_choice
from guardian.config import GUARDIAN_BIN
from guardian.models import ActionSpec, Finding, Severity
from tests.helpers import Recorder


def f(sev=Severity.warn, actions=True, saving=0):
    return Finding(id="abc123abc123", kind="disk_high", severity=sev, title="Disk 86% full", reason="why",
                   est_saving_bytes=saving, actions=[ActionSpec(name="apt_clean")] if actions else [])


def has(cmd, *seq):
    return any(cmd[i:i + len(seq)] == list(seq) for i in range(len(cmd)))


def test_warn_has_buttons_and_15min_expiry():
    cmd = notify_cmd(f(saving=4_100_000_000))
    assert cmd[0] == "notify-send"
    assert has(cmd, "-A", "do=Do it") and has(cmd, "-A", "skip=Skip 24h") and has(cmd, "-A", "details=Details")
    assert has(cmd, "-t", "900000") and has(cmd, "-u", "normal")
    assert cmd[-2] == "Guardian: Disk 86% full"
    assert "4.1 GB" in cmd[-1]


def test_no_do_button_without_actions():
    assert not has(notify_cmd(f(actions=False)), "-A", "do=Do it")


def test_critical_is_urgent_and_does_not_expire():
    cmd = notify_cmd(f(sev=Severity.critical))
    assert has(cmd, "-u", "critical") and "-t" not in cmd


def test_parse_choice():
    assert parse_choice("do\n") == "do"
    assert parse_choice("details") == "details"
    assert parse_choice("") == "timeout"
    assert parse_choice("rm -rf") == "timeout"


def test_launch_asker_uses_named_transient_unit():
    rec = Recorder()
    launch_asker("abc123abc123", run=rec)
    cmd = rec.calls[0]
    assert cmd[:3] == ["systemd-run", "--user", "--collect"]
    assert "--unit=guardian-ask-abc123abc123" in cmd
    assert cmd[-3:] == [str(GUARDIAN_BIN), "ask", "abc123abc123"]


def test_side_effect_helpers_swallow_errors():
    launch_asker("x", run=Recorder(exc=OSError("no systemd")))
    notify_plain("t", "b", run=Recorder(exc=subprocess.TimeoutExpired("notify-send", 10)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ask.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.ask'`.

- [ ] **Step 3: Implement**

`guardian/ask.py`:
```python
import os
import subprocess

from .config import GUARDIAN_BIN
from .models import Finding, Severity
from .units import human

CHOICES = ("do", "skip", "details")
WARN_EXPIRE_MS = "900000"  # 15 min, then counts as "timeout"


def notify_cmd(f: Finding) -> list[str]:
    body = f.reason + (f"\nEstimated saving: {human(f.est_saving_bytes)}" if f.est_saving_bytes else "")
    cmd = ["notify-send", "-a", "Guardian"]
    if f.severity in (Severity.critical, Severity.emergency):
        cmd += ["-u", "critical"]
    else:
        cmd += ["-u", "normal", "-t", WARN_EXPIRE_MS]
    if f.actions:
        cmd += ["-A", "do=Do it"]
    cmd += ["-A", "skip=Skip 24h", "-A", "details=Details", f"Guardian: {f.title}", body]
    return cmd


def parse_choice(stdout: str) -> str:
    lines = stdout.strip().splitlines()
    choice = lines[-1].strip() if lines else ""
    return choice if choice in CHOICES else "timeout"


def notify_plain(title: str, body: str, run=subprocess.run) -> None:
    try:
        run(["notify-send", "-a", "Guardian", title, body], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def launch_asker(fid: str, run=subprocess.run) -> None:
    """Detached unit that blocks on the notification; a duplicate name fails harmlessly (one asker per finding)."""
    try:
        run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=guardian-ask-{fid}",
             str(GUARDIAN_BIN), "ask", fid], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def open_review(spawn=subprocess.Popen) -> None:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    spawn(["x-terminal-emulator", "-e",
           f"bash -c '{GUARDIAN_BIN} review; read -p \"Press Enter to close\"'"], env=env)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ask.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/ask.py tests/test_ask.py
git commit -m "feat: notification buttons and detached asker launch"
```

---

### Task 9: Orchestration flows

**Files:**
- Create: `guardian/flows.py`
- Modify: `tests/helpers.py` (append `Clock`, `Harness`)
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: everything above — `State`, `evaluate`, `proc_key`, `finding_id`, `execute`, `ActionContext`, `advise`, `deep_scan`, probes, ask helpers.
- Produces: `Deps` dataclass (fields `probe_ram, probe_disk, probe_containers, deep_scan, advise, launch_asker, notify, spawn_review, ctx, now`); `run_once(state: State, deps: Deps, dry_run: bool = False) -> list[Finding]`; `ask_flow(state, deps, fid: str) -> None`; `act_flow(state, deps, fid: str) -> None`; `review_flow(state, deps, input_fn=input, out=print) -> None`. Meta keys: `scan`, `advice`, `last_advice`, `last_emergency`, `last_digest`. Sample kinds: `avail` (key `mem`), `rss` (key `proc_key`), `cpu` (key container id).

- [ ] **Step 1: Append test harness to `tests/helpers.py`**

```python
# --- appended for Task 9 ---------------------------------------------------
from guardian.actions import ActionContext
from guardian.flows import Deps
from guardian.models import Advice, DeepScan, DiskFacts, RamFacts, Reclaimable

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
            ctx=ActionContext(home=home, run=self.rec, protected=set(), kill_grace=1), now=self.clock,
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
```

Keep the existing `import subprocess` and `Recorder` at the top of the file; the new imports live in the appended block (they import `guardian.flows`, which exists after Step 4, so the suite only goes green again at Step 5).

- [ ] **Step 2: Write failing tests**

`tests/test_flows.py`:
```python
import os

import pytest

from guardian.config import HELPER
from guardian.flows import act_flow, ask_flow, review_flow, run_once
from guardian.models import ActionSpec, Finding, ProcInfo, Reclaimable, Severity, finding_id
from guardian.state import State
from tests.helpers import GB, Harness, Recorder


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "state.db")


def crit_id():
    return finding_id("disk_high", "/", Severity.critical)


# --- run_once -------------------------------------------------------------
def test_warn_launches_one_asker_not_again_within_2h(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86)
    run_once(state, h.deps)
    assert h.asked == [finding_id("disk_high", "/", Severity.warn)]
    h.clock.t += 120
    run_once(state, h.deps)
    assert len(h.asked) == 1


def test_dry_run_asks_and_executes_nothing(tmp_path, state):
    h = Harness(tmp_path, disk_pct=96)
    findings = run_once(state, h.deps, dry_run=True)
    assert findings and h.asked == [] and h.rec.calls == [] and state.pending() == []


def test_emergency_after_10_unanswered_minutes_runs_zero_risk_only(tmp_path, state):
    pip = tmp_path / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "wheel").write_bytes(b"x" * 10)
    keep = tmp_path / "projects" / "keep.txt"
    keep.parent.mkdir()
    keep.write_text("mine")
    h = Harness(tmp_path, disk_pct=96)
    run_once(state, h.deps)
    assert h.asked == [crit_id()] and h.rec.calls == []
    h.clock.t += 600
    run_once(state, h.deps)
    assert h.rec.ran("docker", "builder", "prune", "-af")
    for verb in ("apt-clean", "journal-vacuum", "rotated-logs"):
        assert h.rec.ran("pkexec", HELPER, verb)
    assert not any(c[:2] == ["docker", "stop"] for c in h.rec.calls)
    assert not pip.exists() and keep.exists()
    assert any("emergency" in t for t, _ in h.notes)
    assert len(state.history()) == 7
    calls = len(h.rec.calls)
    h.clock.t += 120
    run_once(state, h.deps)
    assert len(h.rec.calls) == calls  # at most once per 30 min


def test_skip_on_critical_blocks_emergency(tmp_path, state):
    h = Harness(tmp_path, disk_pct=96)
    run_once(state, h.deps)
    state.record_decision(crit_id(), "skip", h.clock())
    h.clock.t += 600
    run_once(state, h.deps)
    assert h.rec.calls == []


def test_leak_detected_across_runs(tmp_path, state):
    h = Harness(tmp_path)
    for i, rss in enumerate((0.5, 1.2, 2.1)):
        h.top = [ProcInfo(pid=4242, start_time=1.0, name="python3", cmdline="python3 x.py",
                          rss=int(rss * GB), uid=os.getuid())]
        run_once(state, h.deps)
        if i < 2:
            h.clock.t += 600
    assert h.asked == [finding_id("leak_suspect", "4242:1.0", Severity.warn)]


def test_deep_scan_cached_6h_and_refreshed_early_when_disk_high(tmp_path, state):
    h = Harness(tmp_path)
    run_once(state, h.deps)
    h.clock.t += 120
    run_once(state, h.deps)
    assert h.scans == 1
    h.clock.t += 6 * 3600
    run_once(state, h.deps)
    assert h.scans == 2
    h.disk_pct = 86
    h.clock.t += 1800
    run_once(state, h.deps)
    assert h.scans == 3


def test_advisor_when_nothing_reclaimable_rate_limited(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86)
    run_once(state, h.deps)
    assert h.advice == 1 and state.meta_get("advice")
    assert any("advice" in t for t, _ in h.notes)
    h.clock.t += 120
    run_once(state, h.deps)
    assert h.advice == 1


def test_advisor_not_called_when_rules_can_reclaim(tmp_path, state):
    h = Harness(tmp_path, disk_pct=86, reclaim=Reclaimable(build_cache=3 * GB))
    run_once(state, h.deps)
    assert h.advice == 0


def test_info_digest_once_per_day(tmp_path, state):
    h = Harness(tmp_path, disk_pct=81)
    run_once(state, h.deps)
    assert len(h.notes) == 1 and h.asked == []
    h.clock.t += 120
    run_once(state, h.deps)
    assert len(h.notes) == 1
    h.clock.t += 86400
    run_once(state, h.deps)
    assert len(h.notes) == 2


def test_probe_crash_acts_on_nothing(tmp_path, state):
    h = Harness(tmp_path, disk_pct=96)

    def boom():
        raise RuntimeError("statvfs failed")

    h.deps.probe_disk = boom
    with pytest.raises(RuntimeError):
        run_once(state, h.deps)
    assert h.rec.calls == [] and h.asked == []


# --- act / ask / review -----------------------------------------------------
def prune_finding(state, now=1.0):
    f = Finding(id="aaaaaaaaaaaa", kind="disk_high", severity=Severity.warn, title="Disk 86% full", reason="r",
                actions=[ActionSpec(name="docker_builder_prune")])
    state.upsert_finding(f, now)
    return f


def test_act_flow_runs_actions_records_history_and_notifies(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder({"docker builder": ("Total reclaimed space: 1GB", 0)}))
    prune_finding(state)
    act_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.history()[0][2:4] == ("docker_builder_prune", 1)
    assert h.notes == [("Guardian: Disk 86% full", "Freed ~1.0 GB.")]
    assert state.pending() == []


def test_act_flow_unknown_finding_is_noop(tmp_path, state):
    h = Harness(tmp_path)
    act_flow(state, h.deps, "nope")
    assert h.rec.calls == [] and h.notes == []


def test_ask_flow_do_runs_actions(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder({"notify-send": ("do\n", 0)}))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.last_decision("aaaaaaaaaaaa") == "do"
    assert h.rec.ran("docker", "builder", "prune", "-af")


@pytest.mark.parametrize("reply", [("", 0), ("", 1)])
def test_ask_flow_timeout_or_notify_failure_never_acts(tmp_path, state, reply):
    h = Harness(tmp_path, rec=Recorder({"notify-send": reply}))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.last_decision("aaaaaaaaaaaa") == "timeout"
    assert not h.rec.ran("docker")


def test_ask_flow_notify_send_missing_never_acts(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder(exc=FileNotFoundError("notify-send")))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert state.last_decision("aaaaaaaaaaaa") == "timeout"


def test_ask_flow_details_opens_review_and_falls_back(tmp_path, state):
    h = Harness(tmp_path, rec=Recorder({"notify-send": ("details\n", 0)}))
    prune_finding(state)
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert h.reviews == 1

    def no_terminal():
        raise FileNotFoundError("x-terminal-emulator")

    h.deps.spawn_review = no_terminal
    ask_flow(state, h.deps, "aaaaaaaaaaaa")
    assert any("guardian review" in b for _, b in h.notes)


def test_review_flow_yes_acts_and_skip_snoozes(tmp_path, state):
    h = Harness(tmp_path)
    prune_finding(state)
    lines = []
    review_flow(state, h.deps, input_fn=lambda _: "y", out=lines.append)
    assert h.rec.ran("docker", "builder", "prune", "-af")
    assert any("Disk 86% full" in line for line in lines)

    state2 = State(tmp_path / "other.db")
    prune_finding(state2)
    review_flow(state2, h.deps, input_fn=lambda _: "s", out=lines.append)
    assert state2.last_decision("aaaaaaaaaaaa") == "skip"


def test_review_flow_nothing_pending(tmp_path, state):
    lines = []
    review_flow(state, Harness(tmp_path).deps, input_fn=lambda _: "y", out=lines.append)
    assert lines == ["Nothing pending."]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_flows.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'guardian.flows'`.

- [ ] **Step 4: Implement**

`guardian/flows.py`:
```python
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
EMERGENCY_GAP = 1800
DIGEST_EVERY = 86400
SAMPLE_KEEP = 86400


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
    hist = History(
        rss=state.samples("rss", now - LEAK_WINDOW - 300),
        avail=state.samples("avail", now - 600).get("mem", []),
        cpu=state.samples("cpu", now - IDLE_WINDOW - 600),
    )
    findings = evaluate(
        Facts(now=now, ram=ram, disk=disk, containers=containers, scan=scan), hist,
        protected=state.protected(),
        critical_unanswered_since=state.unanswered_since(finding_id("disk_high", disk.mount, Severity.critical)),
    )
    if dry_run:
        return findings
    _record_samples(state, ram, containers, now)
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
    _maybe_advise(state, deps, disk, scan, findings, now)
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


def _record_samples(state: State, ram: RamFacts, containers: list[ContainerInfo], now: float) -> None:
    state.add_sample("avail", "mem", now, ram.available)
    for p in ram.top:
        state.add_sample("rss", proc_key(p), now, p.rss)
    for c in containers:
        state.add_sample("cpu", c.id, now, c.cpu_pct)
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


def _maybe_advise(state: State, deps: Deps, disk: DiskFacts, scan: DeepScan | None,
                  findings: list[Finding], now: float) -> None:
    if scan is None or disk.pct < DISK_WARN:
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS (whole suite, since helpers changed).

- [ ] **Step 6: Commit**

```bash
git add guardian/flows.py tests/helpers.py tests/test_flows.py
git commit -m "feat: run/ask/act/review orchestration with emergency path"
```

---

### Task 10: CLI

**Files:**
- Create: `guardian/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `State`, `state_db()`, `Deps`, `run_once`, `ask_flow`, `act_flow`, `review_flow`, `ActionContext`.
- Produces: `default_deps(state: State) -> Deps`; `main(argv: list[str] | None = None) -> int`. Subcommands: `run`, `scan [--dry-run]`, `ask ID`, `act ID`, `review`, `protect NAME`, `history`.

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:
```python
import pytest

from guardian import cli
from guardian.config import state_db
from guardian.state import State
from tests.helpers import Harness


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    h = Harness(tmp_path, disk_pct=86)
    monkeypatch.setattr(cli, "default_deps", lambda state: h.deps)
    return h


def test_scan_prints_findings_and_never_acts(harness, capsys):
    assert cli.main(["scan", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Disk 86% full" in out and "dry run" in out
    assert harness.asked == [] and harness.rec.calls == []


def test_run_launches_asker(harness):
    assert cli.main(["run"]) == 0
    assert len(harness.asked) == 1


def test_protect_and_history(harness, capsys):
    cli.main(["protect", "node"])
    assert State(state_db()).protected() == {"node"}
    State(state_db()).record_history("f", "apt_clean", True, "ok", 0)
    cli.main(["history"])
    assert "apt_clean" in capsys.readouterr().out


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as e:
        cli.main(["explode"])
    assert e.value.code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'cli'`.

- [ ] **Step 3: Implement**

`guardian/cli.py`:
```python
import argparse
import sys
import time

from .actions import ActionContext
from .config import state_db
from .flows import Deps, act_flow, ask_flow, review_flow, run_once
from .state import State


def default_deps(state: State) -> Deps:
    return Deps(ctx=ActionContext(protected=state.protected()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="guardian", description="Disk & process guardian (asks before acting).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="one check cycle (used by the systemd timer)")
    sc = sub.add_parser("scan", help="show current findings; never acts")
    sc.add_argument("--dry-run", action="store_true", help="accepted for clarity; scan never acts")
    for name in ("ask", "act"):
        sub.add_parser(name, help=f"{name} on one finding (used internally)").add_argument("finding_id")
    sub.add_parser("review", help="approve pending findings interactively")
    sub.add_parser("protect", help="never kill processes matching NAME (glob)").add_argument("name")
    sub.add_parser("history", help="show recent actions")
    args = ap.parse_args(argv)

    state = State(state_db())
    deps = default_deps(state)
    if args.cmd == "run":
        run_once(state, deps)
    elif args.cmd == "scan":
        findings = run_once(state, deps, dry_run=True)
        if not findings:
            print("All clear.")
        for f in findings:
            print(f"[{f.severity.value}] {f.title}\n  {f.reason}")
            for a in f.actions:
                print(f"    would: {a.name}{' ' + a.target if a.target else ''}")
        print("(dry run — nothing executed)")
    elif args.cmd == "ask":
        ask_flow(state, deps, args.finding_id)
    elif args.cmd == "act":
        act_flow(state, deps, args.finding_id)
    elif args.cmd == "review":
        review_flow(state, deps)
    elif args.cmd == "protect":
        state.add_protected(args.name)
        print(f"Protected: {args.name}")
    elif args.cmd == "history":
        for at, _fid, action, ok, detail in state.history():
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
            print(f"{stamp}  {'ok  ' if ok else 'FAIL'}  {action}  {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add guardian/cli.py tests/test_cli.py
git commit -m "feat: guardian CLI entrypoint"
```

---

### Task 11: Root helper, polkit rule, systemd units, install/uninstall

**Files:**
- Create: `root-helper/guardian-root-helper`, `root-helper/50-guardian.rules`, `systemd/guardian.service`, `systemd/guardian.timer`, `systemd/guardian-env.desktop`, `install.sh`, `uninstall.sh`
- Test: `tests/test_root_helper.py`

**Interfaces:**
- Consumes: `HELPER` path and verbs (Task 6), console script `guardian` (Task 10).
- Produces: installed `guardian.timer` (every 2 min) running `%h/guardian/.venv/bin/guardian run`; passwordless `pkexec /usr/local/sbin/guardian-root-helper <verb>` for the installing user only.

- [ ] **Step 1: Write failing test**

`tests/test_root_helper.py`:
```python
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HELPER = ROOT / "root-helper" / "guardian-root-helper"


@pytest.mark.parametrize("args", [[], ["rm"], ["apt-clean", "extra"], ["--help"], ["apt-clean;reboot"]])
def test_helper_rejects_anything_but_three_verbs(args):
    p = subprocess.run(["bash", str(HELPER), *args], capture_output=True, text=True)
    assert p.returncode == 2 and "usage" in p.stderr


def test_scripts_parse():
    for script in (HELPER, ROOT / "install.sh", ROOT / "uninstall.sh"):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script


def test_polkit_rule_pins_program_and_user_placeholder():
    rule = (ROOT / "root-helper" / "50-guardian.rules").read_text()
    assert '"/usr/local/sbin/guardian-root-helper"' in rule and "__USER__" in rule
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_root_helper.py -v`
Expected: FAIL (files missing; bash exits 127 / FileNotFoundError).

- [ ] **Step 3: Create files**

`root-helper/guardian-root-helper`:
```bash
#!/bin/bash
# Guardian root helper: exactly three fixed maintenance verbs, nothing else.
set -euo pipefail
usage() { echo "usage: guardian-root-helper {apt-clean|journal-vacuum|rotated-logs}" >&2; exit 2; }
[ "$#" -eq 1 ] || usage
case "$1" in
  apt-clean)      exec /usr/bin/apt-get clean ;;
  journal-vacuum) exec /usr/bin/journalctl --vacuum-size=200M ;;
  rotated-logs)   exec /usr/bin/find /var/log -xdev -type f \( -name '*.gz' -o -name '*.1' \) -mtime +7 -delete ;;
  *) usage ;;
esac
```

`root-helper/50-guardian.rules`:
```js
// Let the Guardian user run the fixed root helper via pkexec without a password.
polkit.addRule(function (action, subject) {
  if (action.id == "org.freedesktop.policykit.exec" &&
      action.lookup("program") == "/usr/local/sbin/guardian-root-helper" &&
      subject.user == "__USER__") {
    return polkit.Result.YES;
  }
});
```

`systemd/guardian.service`:
```ini
[Unit]
Description=Guardian disk & process check

[Service]
Type=oneshot
ExecStart=%h/guardian/.venv/bin/guardian run
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=300
```

`systemd/guardian.timer`:
```ini
[Unit]
Description=Run Guardian every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=15s

[Install]
WantedBy=timers.target
```

`systemd/guardian-env.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=Guardian session environment
Exec=systemctl --user import-environment DISPLAY XAUTHORITY
NoDisplay=true
X-GNOME-Autostart-enabled=true
```

`install.sh`:
```bash
#!/usr/bin/env bash
# Install Guardian: venv, systemd user timer, root helper + polkit rule (one password prompt).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -e .

mkdir -p ~/.config/systemd/user ~/.config/autostart
install -m644 systemd/guardian.service systemd/guardian.timer ~/.config/systemd/user/
install -m644 systemd/guardian-env.desktop ~/.config/autostart/
systemctl --user import-environment DISPLAY XAUTHORITY 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now guardian.timer

rules="$(mktemp)"
trap 'rm -f "$rules"' EXIT
sed "s/__USER__/$USER/" root-helper/50-guardian.rules > "$rules"
echo "Installing root helper + polkit rule (password prompt)..."
pkexec bash -c "install -m755 -o root -g root '$PWD/root-helper/guardian-root-helper' /usr/local/sbin/guardian-root-helper \
  && install -m644 -o root -g root '$rules' /etc/polkit-1/rules.d/50-guardian.rules"
echo "Guardian installed. Check: systemctl --user list-timers guardian.timer"
```

`uninstall.sh`:
```bash
#!/usr/bin/env bash
# Remove Guardian units, autostart entry, root helper and polkit rule. Keeps state for inspection.
set -euo pipefail
systemctl --user disable --now guardian.timer 2>/dev/null || true
rm -f ~/.config/systemd/user/guardian.service ~/.config/systemd/user/guardian.timer \
      ~/.config/autostart/guardian-env.desktop
systemctl --user daemon-reload
pkexec rm -f /usr/local/sbin/guardian-root-helper /etc/polkit-1/rules.d/50-guardian.rules
echo "Removed. State kept at ~/.local/state/guardian (delete it manually if you want)."
```

Run: `chmod +x root-helper/guardian-root-helper install.sh uninstall.sh`

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add root-helper systemd install.sh uninstall.sh tests/test_root_helper.py
git commit -m "feat: root helper, polkit rule, systemd timer, install scripts"
```

---

### Task 12: Integration tests, install, end-to-end verification

**Files:**
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: `execute`, `ActionContext`, `evaluate`, `proc_key`, `rules.LEAK_BYTES`, models.
- Produces: verified, installed system.

- [ ] **Step 1: Write integration tests**

`tests/test_integration.py`:
```python
import os
import subprocess
import sys
import time

import psutil
import pytest

from guardian import rules
from guardian.actions import ActionContext, execute
from guardian.models import ActionSpec, DiskFacts, Facts, History, ProcInfo, RamFacts
from guardian.rules import evaluate, proc_key

pytestmark = pytest.mark.integration


def test_stop_idle_container_for_real():
    cid = subprocess.run(["docker", "run", "-d", "--rm", "alpine", "sleep", "600"],
                         capture_output=True, text=True, check=True).stdout.strip()[:12]
    try:
        time.sleep(3)
        r = execute(ActionSpec(name="stop_container", target=cid), ActionContext())
        assert r.ok, r.detail
        running = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True).stdout.split()
        assert cid not in running
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_leak_detected_and_killed_on_real_process(monkeypatch):
    monkeypatch.setattr(rules, "LEAK_BYTES", 50 * 10**6)
    hog = subprocess.Popen([sys.executable, "-c",
                            "import time\nbuf=[]\nwhile True:\n    buf.append(b'x' * 30_000_000)\n    time.sleep(1)"])
    try:
        ps = psutil.Process(hog.pid)
        hist, flagged = History(), []
        for _ in range(6):
            time.sleep(1.2)
            p = ProcInfo(pid=hog.pid, start_time=ps.create_time(), name=ps.name(),
                         cmdline=" ".join(ps.cmdline()), rss=ps.memory_info().rss, uid=os.getuid())
            now = time.time()
            facts = Facts(now=now, ram=RamFacts(total=10**12, available=10**12, swap_total=0, swap_free=0, top=[p]),
                          disk=DiskFacts(mount="/", used=1, avail=99))
            flagged += [f for f in evaluate(facts, hist, protected=set(), critical_unanswered_since=None)
                        if f.kind == "leak_suspect"]
            hist.rss.setdefault(proc_key(p), []).append((now, float(p.rss)))
            if flagged:
                break
        assert flagged, "growing process was not flagged"
        r = execute(flagged[0].actions[0], ActionContext(kill_grace=2))
        assert r.ok, r.detail
        assert hog.wait(5) is not None
    finally:
        if hog.poll() is None:
            hog.kill()
```

- [ ] **Step 2: Run integration tests**

Run: `.venv/bin/pytest -m integration -v`
Expected: 2 PASS (pulls `alpine` once, ~3 MB).

- [ ] **Step 3: Run the full unit suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS, 0 failures.

- [ ] **Step 4: Install**

Run: `./install.sh` (one polkit password prompt)
Expected: ends with "Guardian installed."; `systemctl --user list-timers guardian.timer` shows a NEXT time within 2 min.

- [ ] **Step 5: Verify root helper needs no password**

Run: `pkexec /usr/local/sbin/guardian-root-helper journal-vacuum`
Expected: journalctl vacuum output, **no** password prompt. Also `pkexec /usr/local/sbin/guardian-root-helper rm` → exit 2 with usage.

- [ ] **Step 6: Verify a real scan and a real timer run**

Run: `.venv/bin/guardian scan` → prints findings or "All clear." and "(dry run — nothing executed)".
Run: `systemctl --user start guardian.service && journalctl --user -u guardian.service -n 20 --no-pager`
Expected: run finishes, no Python traceback.

- [ ] **Step 7: End-to-end click path through the systemd asker**

```bash
docker run -d --name guardian-e2e alpine sleep 3600
.venv/bin/python - <<'EOF'
import subprocess
from guardian.ask import launch_asker
from guardian.config import state_db
from guardian.models import ActionSpec, Finding, Severity
from guardian.state import State
cid = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", "guardian-e2e"],
                     capture_output=True, text=True, check=True).stdout.strip()[:12]
s = State(state_db())
s.upsert_finding(Finding(id="e2e000000001", kind="container_idle", severity=Severity.warn,
                         title="E2E test: stop guardian-e2e", reason="Synthetic finding for the click-path test.",
                         actions=[ActionSpec(name="stop_container", target=cid)]), 0)
launch_asker("e2e000000001")
EOF
```
Expected: a desktop notification "Guardian: E2E test: stop guardian-e2e" with **Do it / Skip 24h / Details**. Click **Do it** → a second notification "Done." (or "Freed ~…"), `docker ps` no longer lists `guardian-e2e`, and `.venv/bin/guardian history` shows `ok  stop_container:<id>`. Then repeat the python block with id `e2e000000002` after `docker start guardian-e2e` and click **Details** → a terminal opens running `guardian review`. Clean up: `docker rm -f guardian-e2e`.

- [ ] **Step 8: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: docker and real-process integration tests"
```
