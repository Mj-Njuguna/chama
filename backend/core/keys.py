"""Versioned secret keys, so a key can be rotated without a flag day.

Each secret is a *key ring*: an environment variable holding one or more ``id:key`` entries,
newest first, e.g. ``AUDIT_HASH_KEYS="k2:<base64url>,k1:<base64url>"``.

- New values are always produced with the first (current) key and carry its ID: ``k2:<hex>``.
- Values made with an older key still verify while that key stays on the ring.
- Rotation: prepend a new key, deploy, wait until nothing depends on the old one, remove it.

Each purpose (audit hashing, OTP pepper, token key) has its own ring; keys are never shared
between rings. Key material never appears in logs or reprs.
"""

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass, field

from django.core.exceptions import ImproperlyConfigured

MIN_KEY_BYTES = 32
_KEY_ID = re.compile(r"^[a-z0-9]{1,16}$")


@dataclass(frozen=True)
class KeyRing:
    name: str
    _keys: tuple[tuple[str, bytes], ...] = field(repr=False)

    @property
    def current_id(self) -> str:
        return self._keys[0][0]

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(key_id for key_id, _ in self._keys)

    def key(self, key_id: str) -> bytes | None:
        return dict(self._keys).get(key_id)

    @staticmethod
    def _mac(key: bytes, purpose: str, data: bytes) -> str:
        # The purpose is part of the message, so one ring never yields the same value for two
        # different uses.
        return hmac.new(key, purpose.encode() + b"\x00" + data, hashlib.sha256).hexdigest()

    def sign(self, purpose: str, data: bytes) -> str:
        """``<key id>:<hex HMAC-SHA256>`` with the current key."""
        key_id, key = self._keys[0]
        return f"{key_id}:{self._mac(key, purpose, data)}"

    def sign_all(self, purpose: str, data: bytes) -> list[str]:
        """The value under every key on the ring, current first (for lookups during rotation)."""
        return [f"{key_id}:{self._mac(key, purpose, data)}" for key_id, key in self._keys]

    def verify(self, purpose: str, data: bytes, signed: str) -> bool:
        """Constant-time check of a value from :meth:`sign`. Unknown key IDs fail."""
        key_id, _, digest = signed.partition(":")
        key = self.key(key_id)
        if key is None or not digest:
            return False
        return hmac.compare_digest(digest, self._mac(key, purpose, data))


def parse_key_ring(name: str, raw: str) -> KeyRing:
    """Parse ``id:base64url,id:base64url`` (newest first). Raises ImproperlyConfigured."""
    entries: list[tuple[str, bytes]] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        key_id, sep, encoded = item.partition(":")
        if not sep or not _KEY_ID.fullmatch(key_id):
            raise ImproperlyConfigured(f"{name}: each entry must be '<id>:<base64url key>'")
        try:
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (binascii.Error, ValueError) as exc:
            raise ImproperlyConfigured(f"{name}: key {key_id!r} is not base64url") from exc
        if len(key) < MIN_KEY_BYTES:
            raise ImproperlyConfigured(f"{name}: key {key_id!r} must be at least 32 bytes")
        if key_id in dict(entries):
            raise ImproperlyConfigured(f"{name}: duplicate key id {key_id!r}")
        entries.append((key_id, key))
    if not entries:
        raise ImproperlyConfigured(f"{name}: at least one key is required")
    return KeyRing(name, tuple(entries))


def dev_key_ring_value(purpose: str) -> str:
    """A fixed, obviously non-secret ring for dev and tests. Prod settings refuse to start
    without the real environment variable, so this can never be used there."""
    key = hashlib.sha256(f"dev-only {purpose} key, never used in production".encode()).digest()
    return "dev1:" + base64.urlsafe_b64encode(key).decode().rstrip("=")
