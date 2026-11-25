"""Phone number normalization to E.164 (invariant 3). The ONE place raw phone input is parsed.

Every boundary that accepts a phone number (OTP login, staff invitations, occupants, payer MSISDN
matching) calls :func:`normalize_phone` and stores only its result. The behaviour is pinned by
``tests/data/phone-vectors.json``, which the mobile app tests against as well.

Rules:

- Only ASCII digits, ``+``, spaces, ``-``, ``.`` and parentheses are accepted. Letters (extensions
  like ``ext 12`` or ``x``), invisible characters and non-ASCII digits are rejected rather than
  silently dropped, so what is stored is exactly what the user typed, minus formatting.
- Numbers without a ``+`` are read in the first country of ``settings.ALLOWED_COUNTRY_CODES``
  (``0712 345 678``, ``712345678`` and ``254712345678`` all mean ``+254712345678``).
- The number must be valid, belong to a country in ``ALLOWED_COUNTRY_CODES`` (MVP: ``["KE"]``)
  and be a mobile number. Fixed lines cannot receive WhatsApp or SMS OTPs.
- ``FIXED_LINE_OR_MOBILE`` (numbering plans where the library cannot tell the two apart; none in
  Kenya today) is rejected as ``not_mobile``: we only send OTPs where the number is known to be
  mobile, which limits OTP cost abuse. Revisit when a country with such a plan is allowed.
"""

import re
from collections.abc import Iterable
from enum import StrEnum

import phonenumbers
from django.conf import settings
from phonenumbers import PhoneNumberFormat, PhoneNumberType

MAX_INPUT_LENGTH = 32
_ALLOWED_CHARS = re.compile(r"[0-9+\s().-]+")


class PhoneError(StrEnum):
    INVALID_FORMAT = "invalid_format"  # empty, too long, unexpected characters, unparseable
    INVALID_NUMBER = "invalid_number"  # parsed, but not a valid number in its numbering plan
    COUNTRY_NOT_ALLOWED = "country_not_allowed"
    NOT_MOBILE = "not_mobile"  # fixed line, toll free, premium rate, ambiguous, ...


class InvalidPhone(ValueError):
    def __init__(self, code: PhoneError) -> None:
        super().__init__(code.value)
        self.code = code


def normalize_phone(raw: str, allowed_countries: Iterable[str] | None = None) -> str:
    """Return ``raw`` as E.164 (``+2547XXXXXXXX``) or raise :class:`InvalidPhone`.

    ``allowed_countries`` defaults to ``settings.ALLOWED_COUNTRY_CODES``; the first entry is the
    region used for numbers written without a country code.
    """
    countries = [c.upper() for c in (allowed_countries or settings.ALLOWED_COUNTRY_CODES)]
    if not countries:
        raise InvalidPhone(PhoneError.COUNTRY_NOT_ALLOWED)

    text = raw.strip() if isinstance(raw, str) else ""
    if (
        not text
        or len(text) > MAX_INPUT_LENGTH
        or not _ALLOWED_CHARS.fullmatch(text)
        or "+" in text[1:]
    ):
        raise InvalidPhone(PhoneError.INVALID_FORMAT)

    try:
        number = phonenumbers.parse(text, countries[0])
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhone(PhoneError.INVALID_FORMAT) from exc

    if not phonenumbers.is_valid_number(number):
        raise InvalidPhone(PhoneError.INVALID_NUMBER)
    if phonenumbers.region_code_for_number(number) not in countries:
        raise InvalidPhone(PhoneError.COUNTRY_NOT_ALLOWED)
    if phonenumbers.number_type(number) != PhoneNumberType.MOBILE:
        raise InvalidPhone(PhoneError.NOT_MOBILE)

    return phonenumbers.format_number(number, PhoneNumberFormat.E164)
