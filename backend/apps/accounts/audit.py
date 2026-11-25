"""Security events: writing and reading the append-only audit table. Internal to
accounts; other modules use the functions re-exported by ``apps.accounts.services``.

What may be stored is deliberately narrow:

- phone numbers and IP addresses only as keyed HMACs (``subject_hash``, ``ip_hash``) from the
  ``AUDIT_HASH_KEYS`` ring, so repeated attempts can be correlated without keeping the value;
- ``metadata`` only with keys from :data:`METADATA_SCHEMA`. String values are short lower-case
  words (letters and underscores, no digits), integers are small. A phone number, OTP or token
  cannot fit either shape, so it can never be written, even by mistake. (``key_id`` has its own
  narrow shape: letters then at most four digits.)
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any

from django.conf import settings

from apps.accounts.models import SecurityEvent, SecurityEventKind
from core.keys import KeyRing, parse_key_ring
from core.tenancy import current_chama_id

_WORD = re.compile(r"^[a-z_]{1,40}$")
MAX_METADATA_INT = 9999

# Allowed metadata keys and the type of their value.
METADATA_SCHEMA: dict[str, type] = {
    "channel": str,  # whatsapp, sms
    "reason": str,  # an error or outcome code
    "role": str,
    "platform": str,  # android, ios
    "attempts": int,
    "level": int,  # lockout escalation level
    "count": int,
    "version": int,  # constitution version
    "threshold": int,
    "operator": str,  # who ran an operator command (a short name, no digits)
    "key_id": str,  # a key ring ID, checked against _KEY_ID_VALUE below
}

# Key IDs ("k1", "k2"...): letters then at most four digits, so no OTP or phone number fits.
_KEY_ID_VALUE = re.compile(r"^[a-z]{1,8}[0-9]{0,4}$")
_PATTERNS: dict[str, re.Pattern[str]] = {"key_id": _KEY_ID_VALUE}

_SUBJECT_PURPOSE = "security-event-subject"
_IP_PURPOSE = "security-event-ip"


class InvalidAuditMetadata(ValueError):
    """Metadata that does not fit METADATA_SCHEMA. The message never includes the value."""


@cache
def _key_ring(raw: str) -> KeyRing:
    return parse_key_ring("AUDIT_HASH_KEYS", raw)


def audit_key_ring() -> KeyRing:
    return _key_ring(settings.AUDIT_HASH_KEYS)


def subject_hash(phone_e164: str) -> str:
    """Keyed HMAC of a normalized phone number, with the current key's ID."""
    return audit_key_ring().sign(_SUBJECT_PURPOSE, phone_e164.encode())


def subject_hashes(phone_e164: str) -> list[str]:
    """The phone's hash under every key on the ring, for lookups across a key rotation."""
    return audit_key_ring().sign_all(_SUBJECT_PURPOSE, phone_e164.encode())


def ip_hash(ip: str) -> str:
    return audit_key_ring().sign(_IP_PURPOSE, ip.encode())


def clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return ``metadata`` if every key and value fits the schema; raise otherwise."""
    cleaned: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        expected = METADATA_SCHEMA.get(key)
        if expected is None:
            raise InvalidAuditMetadata(f"metadata key {key!r} is not allowed")
        if expected is int:
            if type(value) is not int or not 0 <= value <= MAX_METADATA_INT:
                raise InvalidAuditMetadata(f"metadata {key!r} must be an int 0..{MAX_METADATA_INT}")
        elif not isinstance(value, str) or not _PATTERNS.get(key, _WORD).fullmatch(value):
            raise InvalidAuditMetadata(f"metadata {key!r} has a value of the wrong shape")
        cleaned[key] = value
    return cleaned


def record_security_event(
    kind: SecurityEventKind | str,
    *,
    chama_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    phone_e164: str | None = None,
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Append one event. ``chama_id`` must be None or the current chama context (RLS)."""
    event = SecurityEvent.objects.create(
        kind=SecurityEventKind(kind),
        chama_id=chama_id,
        user_id=user_id,
        device_id=device_id,
        session_id=session_id,
        subject_hash=subject_hash(phone_e164) if phone_e164 else "",
        ip_hash=ip_hash(ip) if ip else "",
        metadata=clean_metadata(metadata),
    )
    return event.id


@dataclass(frozen=True)
class SecurityEventSummary:
    id: uuid.UUID
    occurred_at: datetime
    kind: str
    user_id: uuid.UUID | None
    device_id: uuid.UUID | None
    metadata: dict[str, Any]


def chama_security_events(limit: int = 100) -> list[SecurityEventSummary]:
    """The current chama's events, newest first. The caller checks the role (officials only);
    RLS limits the rows to the chama context. No hashes are returned."""
    chama_id = current_chama_id()
    rows = (
        SecurityEvent.objects.filter(chama_id=chama_id)
        .order_by("-occurred_at", "-id")
        .values_list("id", "occurred_at", "kind", "user_id", "device_id", "metadata")[:limit]
    )
    return [SecurityEventSummary(*row) for row in rows]
