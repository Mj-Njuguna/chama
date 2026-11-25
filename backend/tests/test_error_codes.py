"""ERROR_CODES (the OpenAPI enum) stays in sync with the code."""

import re
from pathlib import Path

import yaml

from core.api_errors import _CODES, ERROR_CODES, ERROR_REASONS, PIN_REJECTIONS, STATE_CONFLICTS
from core.phones import PhoneError

BACKEND = Path(__file__).resolve().parent.parent
RESERVED: set[str] = set()  # every code is returned somewhere


def _codes_raised_in_source() -> set[str]:
    found: set[str] = set()
    pattern = re.compile(r'(?:ApiError|token_error)\(\s*"([a-z_]+)"')
    for top in ("apps", "core"):
        for path in (BACKEND / top).rglob("*.py"):
            found |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return found


def test_every_code_the_code_returns_is_in_the_enum():
    returned = _codes_raised_in_source() | set(_CODES.values())
    assert returned - set(ERROR_CODES) == set()


def test_every_enum_code_is_returned_somewhere_or_reserved():
    returned = _codes_raised_in_source() | set(_CODES.values())
    assert set(ERROR_CODES) - returned == RESERVED


def test_enum_has_no_duplicates_and_reasons_are_the_known_sub_codes():
    assert len(set(ERROR_CODES)) == len(ERROR_CODES)
    assert len(set(ERROR_REASONS)) == len(ERROR_REASONS)
    phone = {reason.value for reason in PhoneError}
    assert set(ERROR_REASONS) == phone | set(PIN_REJECTIONS) | set(STATE_CONFLICTS)


def test_committed_schema_publishes_the_enums():
    schema = yaml.safe_load((BACKEND / "openapi" / "schema.yaml").read_text(encoding="utf-8"))
    components = schema["components"]["schemas"]
    assert components["ErrorCodeEnum"]["enum"] == list(ERROR_CODES)
    assert components["ErrorReasonEnum"]["enum"] == list(ERROR_REASONS)


def test_state_conflict_refuses_an_unknown_reason():
    import pytest

    from core.api_errors import state_conflict

    with pytest.raises(ValueError, match="unknown"):
        state_conflict("made_up", "x")
