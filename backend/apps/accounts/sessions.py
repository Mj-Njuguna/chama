"""Device-bound sessions and opaque tokens.

THE ONLY MODULE that reads or writes ``accounts_authsession`` and ``accounts_authtoken``
(``tests/test_sessions.py`` pins it). Both are RLS-exempt because tokens are looked up before
the user is known, so this module is their access control.

- Tokens are opaque: ``chm_at_<43 chars>`` (access, 15 min) and ``chm_rt_<43 chars>`` (refresh,
  30 days, rotated on every use). The ``chm_`` prefix lets secret scanners recognise them. Only
  their SHA-256 hashes are stored.
- A session is one refresh-token family on one device. It ends at the latest after
  ``SESSION_MAX_AGE_SECONDS`` (90 days), however often it is refreshed; then a fresh OTP is
  needed.
- **Rotation.** The first pair is random. Each successor is *derived* from the refresh token
  being rotated: ``HMAC(AUTH_TOKEN_KEYS[current], purpose || previous refresh token)``. Only
  the holder of the previous token (and the server key) can compute it, and nothing in clear
  is stored.
- **Grace window.** The previous refresh token, presented again within
  ``REFRESH_GRACE_SECONDS`` (10) and only while its successor has not itself been rotated,
  gets the **same** successor pair, derived again with the key named in ``rotation_key_id``.
  A second pair is never issued.
- **Reuse.** Any other retired refresh token revokes the whole family, records
  ``refresh_reused`` and answers ``refresh_reused``. If the key that derived the current pair is
  no longer on the ring, a grace replay cannot be answered safely: the family is revoked.
- **Holder checks.** Every refresh and every authenticated request also checks that the user is
  still active and the device not revoked; otherwise ``session_revoked`` (a refresh also closes
  the family, reason ``user_inactive`` or ``device_revoked``).
- **Concurrency.** Rotation locks the session row (``SELECT ... FOR UPDATE``). Two refreshes
  with the same token produce one rotation; the second waits, then takes the grace path.
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from typing import TypeGuard

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.audit import MAX_METADATA_INT, record_security_event
from apps.accounts.models import (
    AuthSession,
    AuthToken,
    Device,
    SecurityEventKind,
    SessionRevokedReason,
    User,
)
from core.api_errors import ApiError
from core.keys import KeyRing, parse_key_ring
from core.logging import get_logger
from core.tenancy import user_context

ACCESS_PREFIX = "chm_at_"
REFRESH_PREFIX = "chm_rt_"

log = get_logger(__name__)

_TOKEN_CHARS = 43  # 32 bytes, base64url without padding


def token_error(code: str, message: str) -> ApiError:
    return ApiError(code, message, status_code=status.HTTP_401_UNAUTHORIZED)


def invalid_token() -> ApiError:
    return token_error("invalid_token", "The token is not valid.")


def session_revoked() -> ApiError:
    return token_error("session_revoked", "This session has ended. Log in again.")


def session_expired() -> ApiError:
    return token_error("session_expired", "This session is too old. Log in again.")


def refresh_reused() -> ApiError:
    return token_error("refresh_reused", "This session has ended for safety. Log in again.")


def token_expired() -> ApiError:
    return token_error("token_expired", "The access token has expired. Refresh it.")


# ---------------------------------------------------------------- token material


@cache
def _ring(raw: str) -> KeyRing:
    return parse_key_ring("AUTH_TOKEN_KEYS", raw)


def _key_ring() -> KeyRing:
    return _ring(settings.AUTH_TOKEN_KEYS)


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _random_pair() -> tuple[str, str]:
    return ACCESS_PREFIX + _encode(secrets.token_bytes(32)), REFRESH_PREFIX + _encode(
        secrets.token_bytes(32)
    )


def _derived_pair(key: bytes, previous_refresh: str) -> tuple[str, str]:
    """The successor pair of ``previous_refresh``: same input and key, same pair."""

    def derive(purpose: bytes) -> str:
        mac = hmac.new(key, purpose + b"\x00" + previous_refresh.encode(), hashlib.sha256)
        return _encode(mac.digest())

    return ACCESS_PREFIX + derive(b"access"), REFRESH_PREFIX + derive(b"refresh")


def _well_formed(token: object, prefix: str) -> TypeGuard[str]:
    return (
        isinstance(token, str)
        and token.startswith(prefix)
        and len(token) == len(prefix) + _TOKEN_CHARS
    )


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class TokenPair:
    session_id: uuid.UUID
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_in: int


@dataclass(frozen=True)
class SessionIdentity:
    session_id: uuid.UUID
    user_id: uuid.UUID
    device_id: uuid.UUID
    active_chama_id: uuid.UUID | None
    # When this session last verified the server PIN / a fresh OTP, or None.
    step_up_pin_at: datetime | None = None
    step_up_otp_at: datetime | None = None
    # When the login OTP was verified: only for setting the first PIN.
    login_verified_at: datetime | None = None
    # When this session began with a code sign-in. Refreshes and chama switches keep it.
    started_at: datetime | None = None


def _seconds(until: datetime, now: datetime) -> int:
    return max(0, int((until - now).total_seconds()))


def _pair(session: AuthSession, access: str, refresh: str, now: datetime) -> TokenPair:
    return TokenPair(
        session_id=session.id,
        access_token=access,
        access_expires_in=_seconds(session.access_expires_at, now),
        refresh_token=refresh,
        refresh_expires_in=_seconds(session.refresh_expires_at, now),
    )


def _set_expiries(session: AuthSession, now: datetime) -> None:
    session.access_expires_at = min(
        now + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS), session.absolute_expires_at
    )
    session.refresh_expires_at = min(
        now + timedelta(seconds=settings.REFRESH_TOKEN_TTL_SECONDS), session.absolute_expires_at
    )


def _holder_revocation(session: AuthSession) -> SessionRevokedReason | None:
    """Why the session's user or device may no longer use it, or None if both are fine."""
    if not User.objects.filter(id=session.user_id, is_active=True).exists():
        return SessionRevokedReason.USER_INACTIVE
    # Trusted caller: the user ID comes from the session row a presented token hash matched.
    with user_context(session.user_id):
        usable = Device.objects.filter(id=session.device_id, revoked_at=None).exists()
    return None if usable else SessionRevokedReason.DEVICE_REVOKED


# ---------------------------------------------------------------- login


@dataclass(frozen=True)
class DeviceInfo:
    installation_id: str
    platform: str
    name: str = ""


def login(user_id: uuid.UUID, device: DeviceInfo, active_chama_id: uuid.UUID | None) -> TokenPair:
    """Bind the device and start a session. Call only after a verified OTP."""
    now = timezone.now()
    with user_context(user_id):  # trusted caller: the code was just verified
        bound, _ = Device.objects.update_or_create(
            user_id=user_id,
            installation_id=device.installation_id,
            defaults={
                "platform": device.platform,
                "name": device.name,
                "last_seen_at": now,
                "revoked_at": None,
            },
        )
        access, refresh = _random_pair()
        session = AuthSession(
            user_id=user_id,
            device_id=bound.id,
            active_chama_id=active_chama_id,
            absolute_expires_at=now + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS),
            access_hash=_hash(access),
            refresh_hash=_hash(refresh),
            # The login OTP was just verified. This is NOT a step-up OTP: it only lets the
            # user set the first PIN within 5 minutes.
            login_verified_at=now,
        )
        _set_expiries(session, now)
        session.save()
        record_security_event(
            SecurityEventKind.LOGIN, user_id=user_id, device_id=bound.id, session_id=session.id
        )
    return _pair(session, access, refresh, now)


# ---------------------------------------------------------------- refresh


def refresh(refresh_token: object) -> TokenPair:
    """Rotate a refresh token (or re-return the pair inside the grace window). See module doc."""
    if not _well_formed(refresh_token, REFRESH_PREFIX):
        raise invalid_token()
    result = _refresh(refresh_token, timezone.now())
    if isinstance(result, ApiError):
        raise result  # after the transaction committed any revocation
    return result


def _refresh(token: str, now: datetime) -> TokenPair | ApiError:
    presented = _hash(token)
    with transaction.atomic():
        session = AuthSession.objects.select_for_update().filter(refresh_hash=presented).first()
        if session is not None:
            return _rotate(session, token, presented, now)
        retired = AuthToken.objects.filter(token_hash=presented).first()
        if retired is None:
            return invalid_token()
        session = AuthSession.objects.select_for_update().get(id=retired.session_id)
        if session.revoked_at is not None:
            return session_revoked()
        holder = _holder_revocation(session)
        if holder is not None:
            _revoke(session, holder, now)
            return session_revoked()
        in_grace = (
            retired.generation == session.refresh_generation - 1
            and now - retired.retired_at <= timedelta(seconds=settings.REFRESH_GRACE_SECONDS)
        )
        if not in_grace:
            _revoke(session, SessionRevokedReason.REFRESH_REUSED, now)
            return refresh_reused()
        key = _key_ring().key(session.rotation_key_id)
        if key is None:  # the deriving key left the ring mid-grace: fail safe
            _revoke(session, SessionRevokedReason.KEY_UNAVAILABLE, now)
            return session_revoked()
        access, refresh_ = _derived_pair(key, token)
        if _hash(refresh_) != session.refresh_hash or _hash(access) != session.access_hash:
            _revoke(session, SessionRevokedReason.REFRESH_REUSED, now)
            return refresh_reused()
        return _pair(session, access, refresh_, now)


def _rotate(
    session: AuthSession, token: str, presented: str, now: datetime
) -> TokenPair | ApiError:
    if session.revoked_at is not None:
        return session_revoked()
    holder = _holder_revocation(session)
    if holder is not None:
        _revoke(session, holder, now)
        return session_revoked()
    if now >= session.absolute_expires_at or now >= session.refresh_expires_at:
        return session_expired()
    ring = _key_ring()
    key = ring.key(ring.current_id)
    if key is None:  # cannot happen: the current ID is always on its own ring
        raise RuntimeError("AUTH_TOKEN_KEYS has no current key")
    access, refresh_ = _derived_pair(key, token)
    AuthToken.objects.create(
        token_hash=presented,
        session_id=session.id,
        generation=session.refresh_generation,
        retired_at=now,
    )
    session.access_hash, session.refresh_hash = _hash(access), _hash(refresh_)
    session.refresh_generation += 1
    session.rotation_key_id = ring.current_id
    session.rotated_at = now
    _set_expiries(session, now)
    session.save()
    return _pair(session, access, refresh_, now)


# ---------------------------------------------------------------- access tokens


def authenticate_access(access_token: object) -> SessionIdentity:
    """The session behind an access token, or a 401 ApiError. Used by the middleware."""
    if not _well_formed(access_token, ACCESS_PREFIX):
        raise invalid_token()
    session = AuthSession.objects.filter(access_hash=_hash(access_token)).first()
    if session is None:
        raise invalid_token()
    now = timezone.now()
    if session.revoked_at is not None or _holder_revocation(session) is not None:
        raise session_revoked()
    if now >= session.absolute_expires_at:
        raise session_expired()
    if now >= session.access_expires_at:
        raise token_expired()
    return SessionIdentity(
        session_id=session.id,
        user_id=session.user_id,
        device_id=session.device_id,
        active_chama_id=session.active_chama_id,
        step_up_pin_at=session.step_up_pin_at,
        step_up_otp_at=session.step_up_otp_at,
        login_verified_at=session.login_verified_at,
        started_at=session.created_at,
    )


# ---------------------------------------------------------------- revocation


def _revoke(session: AuthSession, reason: SessionRevokedReason, now: datetime) -> None:
    session.revoked_at, session.revoked_reason = now, reason
    session.save(update_fields=["revoked_at", "revoked_reason"])
    if reason == SessionRevokedReason.REFRESH_REUSED:
        record_security_event(
            SecurityEventKind.REFRESH_REUSED,
            user_id=session.user_id,
            device_id=session.device_id,
            session_id=session.id,
        )


def logout(identity: SessionIdentity) -> None:
    """End the caller's own session."""
    now = timezone.now()
    with transaction.atomic():
        session = AuthSession.objects.select_for_update().get(id=identity.session_id)
        if session.revoked_at is None:
            _revoke(session, SessionRevokedReason.LOGOUT, now)
        record_security_event(
            SecurityEventKind.LOGOUT,
            user_id=identity.user_id,
            device_id=identity.device_id,
            session_id=identity.session_id,
        )


def logout_all(identity: SessionIdentity) -> int:
    """End every session of the caller. Returns how many were still active."""
    now = timezone.now()
    with transaction.atomic():
        ended = AuthSession.objects.filter(user_id=identity.user_id, revoked_at=None).update(
            revoked_at=now, revoked_reason=SessionRevokedReason.LOGOUT_ALL
        )
        record_security_event(
            SecurityEventKind.LOGOUT_ALL,
            user_id=identity.user_id,
            device_id=identity.device_id,
            session_id=identity.session_id,
            metadata={"count": min(ended, 9999)},
        )
    return ended


def revoke_sessions_derived_with(key_id: str, *, operator: str) -> int:
    """Revoke every live session whose current pair was derived with ``key_id``.

    For a compromised AUTH_TOKEN_KEYS key: with the key and any one observed token of
    a family, the rest of the chain can be computed offline for as long as that key derives it.
    Sessions whose current pair came from another key, or is still the random first pair, are
    out of reach and stay. Returns how many were revoked.

    One ``token_key_revoked`` security event (operator, key ID, count) is written in the same
    transaction. The audit count is capped at MAX_METADATA_INT; the exact number is also logged.
    """
    with transaction.atomic():
        revoked = AuthSession.objects.filter(rotation_key_id=key_id, revoked_at=None).update(
            revoked_at=timezone.now(), revoked_reason=SessionRevokedReason.KEY_UNAVAILABLE
        )
        record_security_event(
            SecurityEventKind.TOKEN_KEY_REVOKED,
            metadata={
                "operator": operator,
                "key_id": key_id,
                "count": min(revoked, MAX_METADATA_INT),
            },
        )
    log.warning("token_key_revoked", key_id=key_id, operator=operator, sessions_revoked=revoked)
    return revoked


# ---------------------------------------------------------------- active chama


def set_active_chama(identity: SessionIdentity, chama_id: uuid.UUID) -> None:
    """Bind the caller's session to ``chama_id``. The caller checked an active membership."""
    AuthSession.objects.filter(id=identity.session_id, revoked_at=None).update(
        active_chama_id=chama_id
    )


def clear_active_chama(identity: SessionIdentity) -> None:
    """Unbind the session's active chama, unless a concurrent switch already replaced it."""
    if identity.active_chama_id is None:
        return
    AuthSession.objects.filter(
        id=identity.session_id, active_chama_id=identity.active_chama_id
    ).update(active_chama_id=None)


# ---------------------------------------------------------------- devices (apps.accounts.devices)


def revoke_device_sessions(user_id: uuid.UUID, device_id: uuid.UUID, now: datetime) -> int:
    """End every live session of one device of the user. Returns how many."""
    return AuthSession.objects.filter(user_id=user_id, device_id=device_id, revoked_at=None).update(
        revoked_at=now, revoked_reason=SessionRevokedReason.DEVICE_REVOKED
    )


def devices_with_live_sessions(user_id: uuid.UUID) -> set[uuid.UUID]:
    """The user's devices that have at least one live session (signed in)."""
    now = timezone.now()
    return set(
        AuthSession.objects.filter(
            user_id=user_id,
            revoked_at=None,
            absolute_expires_at__gt=now,
            refresh_expires_at__gt=now,
        ).values_list("device_id", flat=True)
    )


def live_session_ids(session_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Those of ``session_ids`` that are not revoked and not past either expiry."""
    now = timezone.now()
    return set(
        AuthSession.objects.filter(
            id__in=session_ids,
            revoked_at=None,
            absolute_expires_at__gt=now,
            refresh_expires_at__gt=now,
        ).values_list("id", flat=True)
    )


# ---------------------------------------------------------------- step-up (apps.accounts.pin)


def mark_step_up(identity: SessionIdentity, factor: str, now: datetime) -> None:
    """Record that this session just verified ``factor`` (``pin`` or ``otp``)."""
    column = {"pin": "step_up_pin_at", "otp": "step_up_otp_at"}[factor]
    AuthSession.objects.filter(id=identity.session_id, revoked_at=None).update(**{column: now})
