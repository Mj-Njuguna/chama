"""Checks for short free-text labels people type (names, addresses).

Refuses control, format (zero-width, bidi overrides), line and paragraph separators, private-use,
surrogate and unassigned characters: they hide text or reorder it on screen.
"""

import unicodedata

from rest_framework.exceptions import ValidationError

FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})


def has_invisible_characters(text: str) -> bool:
    return any(unicodedata.category(c) in FORBIDDEN_CATEGORIES for c in text)


def checked_label(raw: str, field: str, max_length: int, *, required: bool) -> str:
    """``raw`` trimmed. Codes in ``field_errors[field]``: ``blank`` (required and empty),
    ``too_long``, ``invalid_characters``. An optional empty value is ``""``."""
    value = raw.strip()
    if not value:
        if required:
            raise ValidationError({field: ["blank"]})
        return ""
    if len(value) > max_length:
        raise ValidationError({field: ["too_long"]})
    if has_invisible_characters(value):
        raise ValidationError({field: ["invalid_characters"]})
    return value
