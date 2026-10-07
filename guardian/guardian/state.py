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

    def findings_since(self, since: float) -> list[tuple[Finding, float, str]]:
        rows = self.db.execute(
            "SELECT json, first_seen, status FROM findings WHERE first_seen>=? OR last_asked>=? OR status='pending' "
            "ORDER BY first_seen", (since, since))
        return [(Finding.model_validate_json(js), first, status) for js, first, status in rows]

    def decisions_since(self, since: float) -> list[tuple]:
        return self.db.execute(
            "SELECT finding_id, choice, at FROM decisions WHERE at>=? ORDER BY at", (since,)).fetchall()

    def history_since(self, since: float) -> list[tuple]:
        return self.db.execute(
            "SELECT at, finding_id, action, ok, detail FROM history WHERE at>=? ORDER BY at", (since,)).fetchall()

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
