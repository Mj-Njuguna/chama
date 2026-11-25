"""Field encryption at rest.

Standard primitives from ``cryptography`` only; nothing home-made:

- **AES-256-GCM** (``AESGCM``), a fresh random 96-bit nonce per value (``os.urandom``), the
  128-bit tag checked on every read. A changed byte, a wrong key or a value copied to another
  column fails with :class:`DecryptionFailed`; nothing is ever returned unauthenticated.
- **Keys** come from the ``FIELD_ENCRYPTION_KEYS`` key ring (``core.keys``: ``id:base64url``,
  newest first), its own ring, never shared. The AES key is derived from the ring entry with
  **HKDF-SHA256** (``info`` names the purpose and version), so ring keys longer than 32 bytes
  are fine and the raw ring key is never used directly.
- **Stored form**: ``fe1:<key id>:<base64url(nonce || ciphertext || tag)>``. New values use the
  current key; values under an older key still decrypt while it stays on the ring. Rotation:
  prepend a key, deploy, run ``just manage reencrypt_fields``, then remove the old key.
- **Associated data** binds a value to its column (``fe1\\x00<table>.<column>``), so a ciphertext
  moved to another encrypted column does not decrypt.

:class:`EncryptedTextField` stores text this way. It supports no lookups except ``isnull``
(the ciphertext is randomised, so equality and search cannot work), and plaintext never reaches
SQL, logs or reprs of the stored value.
"""

import base64
import binascii
import os
from functools import cache
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import models

from core.keys import KeyRing, parse_key_ring

VERSION = "fe1"
NONCE_BYTES = 12
TAG_BYTES = 16
_HKDF_INFO = b"chama field encryption v1"


class DecryptionFailed(Exception):
    """The stored value is malformed, under an unknown key, or failed authentication."""


@cache
def _ring(raw: str) -> KeyRing:
    return parse_key_ring("FIELD_ENCRYPTION_KEYS", raw)


def key_ring() -> KeyRing:
    return _ring(settings.FIELD_ENCRYPTION_KEYS)


@cache
def _aead(ring_key: bytes) -> AESGCM:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        ring_key
    )
    return AESGCM(derived)


def _aad(label: str) -> bytes:
    return VERSION.encode() + b"\x00" + label.encode()


def encrypt(plaintext: str, label: str) -> str:
    """Encrypt ``plaintext`` for the column ``label`` (``<table>.<column>``), current key."""
    ring = key_ring()
    key_id = ring.current_id
    key = ring.key(key_id)
    if key is None:  # cannot happen: the current id always has a key
        raise RuntimeError("FIELD_ENCRYPTION_KEYS has no current key")
    nonce = os.urandom(NONCE_BYTES)
    sealed = _aead(key).encrypt(nonce, plaintext.encode(), _aad(label))
    body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
    return f"{VERSION}:{key_id}:{body}"


def key_id_of(stored: str) -> str:
    """The key ID a stored value was encrypted under (raises DecryptionFailed if malformed)."""
    version, sep, rest = stored.partition(":")
    key_id, sep2, _ = rest.partition(":")
    if version != VERSION or not sep or not sep2 or not key_id:
        raise DecryptionFailed("not an encrypted field value")
    return key_id


def decrypt(stored: str, label: str) -> str:
    key_id = key_id_of(stored)
    key = key_ring().key(key_id)
    if key is None:
        raise DecryptionFailed(f"unknown key id {key_id!r}")
    body = stored.split(":", 2)[2]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        raise DecryptionFailed("not base64url") from None
    if len(raw) < NONCE_BYTES + TAG_BYTES:
        raise DecryptionFailed("too short")
    try:
        plain = _aead(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], _aad(label))
    except InvalidTag:
        raise DecryptionFailed("authentication failed") from None
    return plain.decode()


def needs_reencryption(stored: str) -> bool:
    return key_id_of(stored) != key_ring().current_id


class EncryptedTextField(models.TextField):  # type: ignore[type-arg]
    """Text encrypted at rest with AES-256-GCM (module docstring). Python sees plaintext."""

    description = "Text encrypted at rest (AES-256-GCM)"

    @property
    def label(self) -> str:
        return f"{self.model._meta.db_table}.{self.column}"

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> str | None:
        return None if value is None else decrypt(value, self.label)

    def get_db_prep_save(self, value: Any, connection: Any) -> str | None:
        value = self.get_prep_value(value)
        return None if value is None else encrypt(value, self.label)

    def get_lookup(self, lookup_name: str) -> Any:
        # Randomised ciphertext: equality, ordering and search cannot work. Only isnull can.
        if lookup_name != "isnull":
            return None
        return super().get_lookup(lookup_name)

    def get_transform(self, lookup_name: str) -> Any:
        return None
