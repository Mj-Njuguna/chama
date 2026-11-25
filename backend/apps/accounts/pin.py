"""Server step-up PIN). Internal to identity.

THE ONLY MODULE that reads or writes ``accounts_steppin``. Never conflated with local unlock:
this PIN is verified here, on the server, and only for sensitive actions.

- **Hashing.** ``Argon2id(HMAC-SHA256(PIN_PEPPER_KEYS[k], purpose || user id || PIN))``. The
  Argon2 parameters (settings) are stored in the encoded hash; the pepper key ID next to it. A
  successful check re-hashes when the parameters or the current pepper key changed. A 6-digit
  PIN has a million values, so a stolen table is protected by the pepper (kept outside the
  database), and online guessing by the attempt limits below; the hash cost only slows bulk
  work.
- **Rules.** Exactly six digits; refused: one digit repeated or a repeating 2- or 3-digit block
  (``111111``, ``121212``, ``123123``), a run up or down (``123456``, ``654321``), and the last
  six digits of the user's own phone number.
- **Attempts**. Every wrong PIN counts. ``PIN_MAX_FAILED_ATTEMPTS`` (5) in a row lock
  the PIN for an escalating time (15 min, 1 h, 24 h; the level resets once 24 h pass after
  the last lock ended) and then require a fresh OTP on the session before the next try. A
  fresh OTP gives a fresh run of attempts, but never more than ``PIN_DAILY_FAILURE_CAP`` (10)
  wrong PINs per Nairobi day: reaching it locks until midnight. Every lock writes a security
  event. Wrong attempts are committed before the error is returned.
- **Concurrency.** The row is locked (``SELECT ... FOR UPDATE``) around read, check and
  increment, so parallel guesses queue up and can never exceed the limits.
- **Order.** The per-session rate limit, the daily cap and an active lock are all checked
  before any hashing: a locked PIN costs no Argon2 work.
- **Lock state is per user** (the row), never per session: a new login does not reset it.
- **Re-hash is best effort.** It runs after the successful attempt is committed and the step-up
  is marked; if it fails, the correct PIN still succeeds and nothing counts as wrong.
- **Never logged.** No PIN, hash or pepper reaches a log line or security event metadata.
"""

import hashlib
import hmac
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from functools import cache
from itertools import pairwise
from zoneinfo import ZoneInfo

import structlog
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.audit import MAX_METADATA_INT, record_security_event
from apps.accounts.devices import push_targets_for_user
from apps.accounts.models import SecurityEventKind, StepUpPin, User
from apps.accounts.otp import rate_limited, service_unavailable
from apps.accounts.sessions import SessionIdentity, devices_with_live_sessions, mark_step_up
from apps.notifications.services import send_push
from core.api_errors import ApiError, chama_frozen, cooling_off
from core.keys import KeyRing, parse_key_ring
from core.ratelimit import Limit, RateLimitUnavailable, hit
from core.tenancy import user_context

log = structlog.get_logger(__name__)

_PURPOSE = b"step-up-pin"
PIN_LENGTH = 6


# ---------------------------------------------------------------- errors


def pin_not_set() -> ApiError:
    return ApiError(
        "pin_not_set",
        "Set a server PIN first.",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def pin_already_set() -> ApiError:
    return ApiError("pin_already_set", "A PIN is already set. Change it instead.", status_code=409)


def pin_rejected(reason: str) -> ApiError:
    return ApiError("pin_rejected", "Choose a different PIN.", reason=reason)


def invalid_pin(attempts_left: int) -> ApiError:
    return ApiError("invalid_pin", "The PIN is not correct.", attempts_left=attempts_left)


def pin_locked(retry_after: int) -> ApiError:
    return ApiError(
        "pin_locked",
        "Too many wrong PINs. Try again later.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after=retry_after,
    )


def step_up_required(factors: list[str]) -> ApiError:
    return ApiError(
        "step_up_required",
        "Confirm it is you to continue.",
        status_code=status.HTTP_403_FORBIDDEN,
        factors=factors,
    )


# ---------------------------------------------------------------- rules


def rejection_reason(pin: str, phone_e164: str) -> str | None:
    """Why ``pin`` may not be used, or None. Reasons: core.api_errors.ERROR_REASONS."""
    if len(pin) != PIN_LENGTH or not pin.isascii() or not pin.isdigit():
        return "not_six_digits"
    if any(pin == pin[:size] * (PIN_LENGTH // size) for size in (1, 2, 3)):
        return "repeated"
    steps = {int(b) - int(a) for a, b in pairwise(pin)}
    if steps in ({1}, {-1}):
        return "sequence"
    if pin == phone_e164[-PIN_LENGTH:]:
        return "phone_digits"
    return None


# ---------------------------------------------------------------- hashing


@cache
def _ring(raw: str) -> KeyRing:
    return parse_key_ring("PIN_PEPPER_KEYS", raw)


def _pepper_ring() -> KeyRing:
    return _ring(settings.PIN_PEPPER_KEYS)


def _hasher() -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.PIN_ARGON2_TIME_COST,
        memory_cost=settings.PIN_ARGON2_MEMORY_KIB,
        parallelism=settings.PIN_ARGON2_PARALLELISM,
        type=Type.ID,
    )


def _peppered(key: bytes, user_id: uuid.UUID, pin: str) -> str:
    message = _PURPOSE + b"\x00" + user_id.bytes + pin.encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _hash(user_id: uuid.UUID, pin: str) -> tuple[str, str]:
    """``(argon2 encoded hash, pepper key id)`` with the current settings and key."""
    ring = _pepper_ring()
    key = ring.key(ring.current_id)
    if key is None:  # cannot happen: the current ID is always on its own ring
        raise RuntimeError("PIN_PEPPER_KEYS has no current key")
    return _hasher().hash(_peppered(key, user_id, pin)), ring.current_id


def _matches(row: StepUpPin, pin: str) -> bool:
    key = _pepper_ring().key(row.pepper_key_id)
    if key is None:
        return False
    try:
        return _hasher().verify(row.pin_hash, _peppered(key, row.user_id, pin))
    except (VerificationError, InvalidHashError):
        return False


def _needs_rehash(row: StepUpPin) -> bool:
    return row.pepper_key_id != _pepper_ring().current_id or _hasher().check_needs_rehash(
        row.pin_hash
    )


# ---------------------------------------------------------------- attempts


def _as_user(user_id: uuid.UUID) -> AbstractContextManager[uuid.UUID]:
    """The one ``user_context`` call site of this module (pinned by test_user_context)."""
    return user_context(user_id)  # trusted: the verified session's user


def _next_midnight(now: datetime) -> datetime:
    local = timezone.localtime(now, ZoneInfo(settings.BUSINESS_TIME_ZONE))
    return local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _seconds(until: datetime, now: datetime) -> int:
    return max(1, int((until - now).total_seconds()))


def _otp_fresh_since(identity: SessionIdentity, since: datetime | None, now: datetime) -> bool:
    at = identity.step_up_otp_at
    if at is None or now - at > timedelta(seconds=settings.STEP_UP_OTP_FRESH_SECONDS):
        return False
    return since is None or at > since


def _login_fresh(identity: SessionIdentity, now: datetime) -> bool:
    """The login OTP behind this session was verified within the OTP window. Used ONLY for
    setting the first PIN; it never counts as a step-up OTP for an action."""
    at = identity.login_verified_at
    return at is not None and now - at <= timedelta(seconds=settings.STEP_UP_OTP_FRESH_SECONDS)


def _lock(row: StepUpPin, until: datetime, kind: SecurityEventKind, now: datetime) -> ApiError:
    row.locked_until, row.last_locked_at = until, now
    row.needs_otp, row.failed_attempts = True, 0
    row.save()
    record_security_event(kind, user_id=row.user_id, metadata={"level": row.lock_level})
    return pin_locked(_seconds(until, now))


def _attempt(row: StepUpPin, identity: SessionIdentity, pin: str, now: datetime) -> ApiError | None:
    """Check one attempt and update ``row`` (locked by the caller). Returns the error to raise
    after commit, or None. Every refusal that needs no PIN comes before ``_matches`` (hashing)."""
    today = timezone.localtime(now, ZoneInfo(settings.BUSINESS_TIME_ZONE)).date()
    if row.day != today:
        row.day, row.day_failures = today, 0
    quiet = timedelta(hours=settings.PIN_LOCKOUT_RESET_HOURS)
    if row.locked_until and now - row.locked_until > quiet:
        row.lock_level = 0
    cap = settings.PIN_DAILY_FAILURE_CAP
    if row.day_failures >= cap:
        return pin_locked(_seconds(_next_midnight(now), now))
    if row.locked_until and now < row.locked_until:
        return pin_locked(_seconds(row.locked_until, now))
    if _pepper_ring().key(row.pepper_key_id) is None:
        return pin_not_set()  # its pepper key was removed: the PIN must be set again
    if row.needs_otp:
        if not _otp_fresh_since(identity, row.last_locked_at, now):
            row.save()
            return step_up_required(["otp", "pin"])
        row.needs_otp, row.failed_attempts = False, 0  # a fresh run, still under the daily cap
    if not _matches(row, pin):
        row.failed_attempts += 1
        row.day_failures += 1
        if row.day_failures >= cap:
            return _lock(row, _next_midnight(now), SecurityEventKind.PIN_DAILY_CAP, now)
        if row.failed_attempts >= settings.PIN_MAX_FAILED_ATTEMPTS:
            steps = settings.PIN_LOCKOUT_STEPS_SECONDS
            duration = steps[min(row.lock_level, len(steps) - 1)]
            row.lock_level += 1
            return _lock(row, now + timedelta(seconds=duration), SecurityEventKind.PIN_LOCKED, now)
        row.save()
        left = min(settings.PIN_MAX_FAILED_ATTEMPTS - row.failed_attempts, cap - row.day_failures)
        return invalid_pin(left)
    row.failed_attempts = 0
    row.save()
    return None


def _check_session_limit(identity: SessionIdentity) -> None:
    """PIN checks per session (``PIN_VERIFY_LIMIT_PER_SESSION``), before any database or hashing
    work. On top of the per-user lockout: it bounds the Argon2 cost one session can cause.
    Redis down: fail closed, like OTP requests."""
    limit = Limit("pin-session", *settings.PIN_VERIFY_LIMIT_PER_SESSION)
    try:
        verdict = hit(limit, str(identity.session_id))
    except RateLimitUnavailable:
        log.error("pin_rate_limit_store_unavailable")
        raise service_unavailable() from None
    if not verdict.allowed:
        raise rate_limited(verdict.retry_after)


def _rehash_if_needed(user_id: uuid.UUID, pin: str) -> None:
    """Best effort, after a successful check is committed: adopt the current Argon2 parameters
    and pepper key. A failure is logged (without the PIN) and never fails the check."""
    try:
        with transaction.atomic(), _as_user(user_id):
            row = StepUpPin.objects.select_for_update().filter(user_id=user_id).first()
            if row is None or not _needs_rehash(row):
                return
            row.pin_hash, row.pepper_key_id = _hash(user_id, pin)
            row.save(update_fields=["pin_hash", "pepper_key_id"])
    except Exception:  # deliberately broad: the PIN was already accepted
        log.warning("pin_rehash_failed")


# ---------------------------------------------------------------- public (within identity)


def has_pin(user_id: uuid.UUID) -> bool:
    """A PIN that can be checked: a row whose pepper key is still on the ring. A PIN whose key
    was removed counts as no PIN (``pin_not_set``; set a new one with a fresh OTP)."""
    with _as_user(user_id):
        rows = StepUpPin.objects.filter(user_id=user_id)
        key_id = rows.values_list("pepper_key_id", flat=True).first()
    return key_id is not None and _pepper_ring().key(key_id) is not None


def verify_pin(identity: SessionIdentity, pin: object, *, rehash: bool = True) -> None:
    """Check the caller's PIN; on success the session's PIN step-up is fresh. Raises ApiError."""
    _check_session_limit(identity)
    now = timezone.now()
    with transaction.atomic(), _as_user(identity.user_id):
        # The row lock serialises parallel attempts for this user: read, check, increment.
        row = StepUpPin.objects.select_for_update().filter(user_id=identity.user_id).first()
        if row is None:
            raise pin_not_set()
        error = _attempt(row, identity, str(pin), now)
    if error is not None:
        raise error  # the attempt is already committed
    mark_step_up(identity, "pin", now)
    if rehash:
        _rehash_if_needed(identity.user_id, str(pin))


def _phone(user_id: uuid.UUID) -> str:
    return User.objects.values_list("phone", flat=True).get(id=user_id)


def set_first_pin(identity: SessionIdentity, pin: str) -> None:
    """Set the caller's first PIN. Needs a fresh step-up OTP on this session, or a login verified
    within the OTP window (the only use of the login time). Writes ``pin_set`` with ``count``,
    the number of the user's OTHER signed-in devices; when there are any, each gets a push.

    A PIN whose pepper key was removed from the ring counts as no PIN: it is replaced here,
    updating the row (never deleting it), with a fresh STEP-UP OTP only (the login time does not
    count). Lock state is reset; the day's failure count is kept. The event has ``reason``
    ``pepper_key_missing``. This path is OTP-only.
    """
    now = timezone.now()
    if reason := rejection_reason(pin, _phone(identity.user_id)):
        raise pin_rejected(reason)
    otp_fresh = _otp_fresh_since(identity, None, now)
    if not (otp_fresh or _login_fresh(identity, now)):
        raise step_up_required(["otp"])
    pin_hash, key_id = _hash(identity.user_id, pin)
    others = devices_with_live_sessions(identity.user_id) - {identity.device_id}
    metadata: dict[str, object] = {"count": min(len(others), MAX_METADATA_INT)}
    with transaction.atomic(), _as_user(identity.user_id):
        row = StepUpPin.objects.select_for_update().filter(user_id=identity.user_id).first()
        if row is None:
            StepUpPin.objects.create(
                user_id=identity.user_id, pin_hash=pin_hash, pepper_key_id=key_id, set_at=now
            )
        elif _pepper_ring().key(row.pepper_key_id) is not None:
            raise pin_already_set()
        elif not otp_fresh:
            raise step_up_required(["otp"])  # a replacement never rides on the login time
        else:
            row.pin_hash, row.pepper_key_id, row.set_at = pin_hash, key_id, now
            row.failed_attempts, row.lock_level, row.needs_otp = 0, 0, False
            row.locked_until = None
            row.save()  # day and day_failures are kept: the daily cap still holds
            metadata["reason"] = "pepper_key_missing"
        record_security_event(
            SecurityEventKind.PIN_SET,
            user_id=identity.user_id,
            device_id=identity.device_id,
            session_id=identity.session_id,
            metadata=metadata,
        )
    mark_step_up(identity, "pin", now)
    if others:
        _alert_other_devices(identity, others)


def _alert_other_devices(
    identity: SessionIdentity, devices: set[uuid.UUID], kind: str = "pin_set"
) -> None:
    """Push ``kind`` (no other data) to the user's other signed-in devices. Best effort: the
    change and its security event are already committed."""
    targets = [
        (t.token_type, t.token)
        for t in push_targets_for_user(identity.user_id)
        if t.device_id in devices
    ]
    send_push(targets, kind)


# ---------------------------------------------------------------- forgot PIN


def reset_not_found() -> ApiError:
    return ApiError("not_found", "No PIN reset is waiting.", status_code=404)


def reset_effective_at(user_id: uuid.UUID, now: datetime) -> datetime | None:
    """When the user's open PIN reset becomes completable, or None (no open reset)."""
    with _as_user(user_id):
        row = (
            StepUpPin.objects.filter(user_id=user_id, reset_expires_at__gt=now)
            .values_list("reset_effective_at", flat=True)
            .first()
        )
    return row


def _clear_reset(row: StepUpPin) -> None:
    row.reset_pin_hash = row.reset_pepper_key_id = ""
    row.reset_requested_at = row.reset_effective_at = row.reset_expires_at = None


def _pin_event(kind: SecurityEventKind, identity: SessionIdentity) -> None:
    record_security_event(
        kind, user_id=identity.user_id, device_id=identity.device_id, session_id=identity.session_id
    )


def request_reset(identity: SessionIdentity, new_pin: str) -> tuple[datetime, datetime]:
    """Forgot PIN: never completes on an OTP alone. Needs a fresh STEP-UP OTP (not the login),
    stores the new PIN's hash as pending for ``PIN_RESET_COOLING_OFF_HOURS``, alerts the user's
    other signed-in devices. The current PIN keeps working meanwhile. Returns
    ``(effective_at, expires_at)``."""
    now = timezone.now()
    if reason := rejection_reason(new_pin, _phone(identity.user_id)):
        raise pin_rejected(reason)
    if not _otp_fresh_since(identity, None, now):
        raise step_up_required(["otp"])
    pin_hash, key_id = _hash(identity.user_id, new_pin)
    with transaction.atomic(), _as_user(identity.user_id):
        row = StepUpPin.objects.select_for_update().filter(user_id=identity.user_id).first()
        if row is None:
            raise pin_not_set()  # nothing to reset: set the first PIN instead
        if row.reset_expires_at and now < row.reset_expires_at:
            raise cooling_off(_seconds(row.reset_effective_at or now, now))  # cancel it first
        effective = now + timedelta(hours=settings.PIN_RESET_COOLING_OFF_HOURS)
        row.reset_pin_hash, row.reset_pepper_key_id = pin_hash, key_id
        row.reset_requested_at, row.reset_effective_at = now, effective
        row.reset_expires_at = effective + timedelta(hours=settings.PENDING_CHANGE_EXPIRY_HOURS)
        row.save()
        _pin_event(SecurityEventKind.PIN_RESET_REQUESTED, identity)
        expires = row.reset_expires_at
    _alert_other_devices(
        identity,
        devices_with_live_sessions(identity.user_id) - {identity.device_id},
        "pin_reset_requested",
    )
    return effective, expires


def complete_reset(identity: SessionIdentity, frozen: Callable[[], bool]) -> None:
    """After the cooling-off and before expiry, with a fresh step-up OTP: the pending PIN
    replaces the current one by updating the row. Lock state is reset; the day's failure count
    is kept. ``frozen()`` (whether a chama the user owns is frozen; it locks those chama
    rows) runs inside the same transaction, after the row lock: a freeze always wins."""
    now = timezone.now()
    if not _otp_fresh_since(identity, None, now):
        raise step_up_required(["otp"])
    with transaction.atomic(), _as_user(identity.user_id):
        row = StepUpPin.objects.select_for_update().filter(user_id=identity.user_id).first()
        if row is None or row.reset_expires_at is None or now >= row.reset_expires_at:
            raise reset_not_found()
        if row.reset_effective_at and now < row.reset_effective_at:
            raise cooling_off(_seconds(row.reset_effective_at, now))
        if frozen():
            raise chama_frozen()
        row.pin_hash, row.pepper_key_id = row.reset_pin_hash, row.reset_pepper_key_id
        row.set_at = now
        row.failed_attempts, row.lock_level, row.needs_otp, row.locked_until = 0, 0, False, None
        _clear_reset(row)
        row.save()  # day and day_failures are kept: the daily cap still holds
        _pin_event(SecurityEventKind.PIN_RESET_COMPLETED, identity)
    mark_step_up(identity, "pin", now)
    _alert_other_devices(
        identity,
        devices_with_live_sessions(identity.user_id) - {identity.device_id},
        "pin_reset_completed",
    )


def cancel_reset(identity: SessionIdentity) -> None:
    """Any of the user's signed-in sessions, no step-up: stopping a reset is always easy."""
    now = timezone.now()
    with transaction.atomic(), _as_user(identity.user_id):
        row = StepUpPin.objects.select_for_update().filter(user_id=identity.user_id).first()
        if row is None or row.reset_expires_at is None or now >= row.reset_expires_at:
            raise reset_not_found()
        _clear_reset(row)
        row.save()
        _pin_event(SecurityEventKind.PIN_RESET_CANCELLED, identity)
    _alert_other_devices(
        identity,
        devices_with_live_sessions(identity.user_id) - {identity.device_id},
        "pin_reset_cancelled",
    )


def change_pin(identity: SessionIdentity, current: object, new: str) -> None:
    """Change the PIN; needs the current one (counted like any attempt)."""
    if reason := rejection_reason(new, _phone(identity.user_id)):
        raise pin_rejected(reason)  # checked first, so a bad new PIN costs no attempt
    verify_pin(identity, current, rehash=False)  # the new PIN is hashed fresh below
    now = timezone.now()
    pin_hash, key_id = _hash(identity.user_id, new)
    with transaction.atomic(), _as_user(identity.user_id):
        StepUpPin.objects.filter(user_id=identity.user_id).update(
            pin_hash=pin_hash, pepper_key_id=key_id, set_at=now, failed_attempts=0
        )
        record_security_event(
            SecurityEventKind.PIN_CHANGED,
            user_id=identity.user_id,
            device_id=identity.device_id,
            session_id=identity.session_id,
        )
