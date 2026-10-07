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
