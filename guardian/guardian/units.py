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
