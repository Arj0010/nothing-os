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
