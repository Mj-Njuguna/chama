"""Natural sort keys: ``A2`` sorts before ``A10``.

List sorts must use real NOT NULL columns, so a model that sorts names naturally stores
``natural_key(name)`` in an indexed column next to the name and refreshes it on every save.

The key is the lower-cased name with every run of digits replaced by its length (two digits)
followed by the digits without leading zeros. Numbers then compare by size: ``2`` becomes
``012`` and ``10`` becomes ``0210``, and ``012 < 0210``. ``A01`` and ``A1`` get the same key;
the list's id tiebreaker orders them.
"""

import re

_DIGITS = re.compile(r"\d+")


def _number(match: re.Match[str]) -> str:
    digits = match.group().lstrip("0") or "0"
    return f"{len(digits):02d}{digits}"


def natural_key(name: str) -> str:
    """The stored sort key for ``name`` (never empty for a non-empty name)."""
    return _DIGITS.sub(_number, name.lower())
