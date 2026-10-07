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
