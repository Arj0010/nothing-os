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
    assert rec.calls[0][1:] == ["-p", "--model", "haiku", "--output-format", "json"]
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
