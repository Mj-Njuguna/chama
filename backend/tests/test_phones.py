"""Invariant 3: phone normalization, pinned by the contract shared with mobile."""

import json
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from core.phones import InvalidPhone, PhoneError, normalize_phone

# Shared with mobile (mobile tests read the same file). Missing file = failure, never a skip.
_VECTORS_FILE = Path(__file__).parent / "data" / "phone-vectors.json"
_CONTRACT = json.loads(_VECTORS_FILE.read_text(encoding="utf-8"))
_VECTORS: list[dict[str, Any]] = _CONTRACT["vectors"]


def _id(vector: dict[str, Any]) -> str:
    return f"{vector['input']!r}->{vector.get('expected') or vector['error']}"


@pytest.mark.parametrize("vector", _VECTORS, ids=[_id(v) for v in _VECTORS])
def test_phone_vector(vector: dict[str, Any]) -> None:
    countries = vector.get("allowed_countries", _CONTRACT["default_allowed_countries"])
    assert ("expected" in vector) != ("error" in vector), "exactly one of expected/error"
    if "expected" in vector:
        assert normalize_phone(vector["input"], countries) == vector["expected"]
    else:
        with pytest.raises(InvalidPhone) as exc:
            normalize_phone(vector["input"], countries)
        assert exc.value.code == PhoneError(vector["error"])


def test_contract_covers_every_error_code() -> None:
    codes = {v["error"] for v in _VECTORS if "error" in v}
    assert codes == {code.value for code in PhoneError}


def test_contract_default_matches_settings() -> None:
    assert settings.ALLOWED_COUNTRY_CODES == ["KE"]
    assert _CONTRACT["default_allowed_countries"] == settings.ALLOWED_COUNTRY_CODES


def test_defaults_to_the_setting(settings) -> None:
    assert normalize_phone("0712345678") == "+254712345678"
    settings.ALLOWED_COUNTRY_CODES = ["TZ"]
    with pytest.raises(InvalidPhone) as exc:
        normalize_phone("+254712345678")
    assert exc.value.code is PhoneError.COUNTRY_NOT_ALLOWED


def test_empty_allowlist_rejects_everything(settings) -> None:
    settings.ALLOWED_COUNTRY_CODES = []
    with pytest.raises(InvalidPhone) as exc:
        normalize_phone("+254712345678")
    assert exc.value.code is PhoneError.COUNTRY_NOT_ALLOWED


@pytest.mark.parametrize("value", [None, 712345678, b"0712345678"])
def test_non_string_input_is_rejected(value: Any) -> None:
    with pytest.raises(InvalidPhone) as exc:
        normalize_phone(value)
    assert exc.value.code is PhoneError.INVALID_FORMAT


def test_error_message_never_contains_the_number() -> None:
    # Exceptions end up in logs and error trackers.
    with pytest.raises(InvalidPhone) as exc:
        normalize_phone("0201234567")
    assert "0201234567" not in str(exc.value)
    assert str(exc.value) == "not_mobile"


# ---------------------------------------------------------------- library upgrade guard
# The vectors and the prefix scan below were reviewed against this phonenumbers release. Any
# upgrade fails here on purpose: re-run the vectors, review the scan output, then bump this value.
VECTORS_REVIEWED_WITH = "9.0.43"


def test_phonenumbers_upgrade_requires_vector_review() -> None:
    import phonenumbers

    assert phonenumbers.__version__ == VECTORS_REVIEWED_WITH, (
        f"phonenumbers changed to {phonenumbers.__version__}: re-run tests/test_phones.py, check "
        "that no Kenyan mobile range changed type (test_kenyan_prefix_types), review "
        "tests/data/phone-vectors.json, then update VECTORS_REVIEWED_WITH."
    )


def _kenyan_prefix_types() -> dict[str, set[str]]:
    """Number type of every valid 4-digit Kenyan prefix (0 + first 3 national digits)."""
    import phonenumbers
    from phonenumbers import PhoneNumberType

    names = {v: k for k, v in vars(PhoneNumberType).items() if k.isupper()}
    types: dict[str, set[str]] = {}
    for prefix in range(100, 1000):
        for suffix in ("000000", "123456", "555555", "999999"):
            number = phonenumbers.parse(f"+254{prefix}{suffix}")
            if phonenumbers.is_valid_number(number):
                types.setdefault(f"0{prefix}", set()).add(names[phonenumbers.number_type(number)])
    return types


def test_kenyan_prefix_types() -> None:
    types = _kenyan_prefix_types()
    ambiguous = sorted(p for p, t in types.items() if "FIXED_LINE_OR_MOBILE" in t or len(t) > 1)
    assert ambiguous == [], "no Kenyan prefix may be ambiguous or mixed"
    mobile = {p for p, t in types.items() if t == {"MOBILE"}}
    # Every 07xx prefix and the 010x/011x ranges must stay mobile.
    assert {f"07{n:02d}" for n in range(100)} <= mobile
    assert {f"010{n}" for n in range(9)} | {f"011{n}" for n in range(10)} <= mobile
    assert types["0800"] == {"TOLL_FREE"}
    assert types["0900"] == {"PREMIUM_RATE"}
