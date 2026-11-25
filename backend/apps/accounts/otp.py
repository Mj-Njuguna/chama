"""One-time codes: request and verify. Internal to identity; the HTTP
views and the session service call these.

Every number is treated alike, registered or not: requesting a code never looks up users, and
verifying looks a user up (or creates one) only after the code is right. Lockouts, cooldowns
and responses are therefore the same for any number.

Errors are :class:`core.api_errors.ApiError` with stable codes: ``invalid_phone``,
``rate_limited``, ``locked``, ``service_unavailable`` and ``invalid_code``. ``invalid_code`` is the
single answer for a wrong, expired, used, superseded or unknown challenge.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import redis
import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.audit import MAX_METADATA_INT, record_security_event
from apps.accounts.models import (
    OtpChallenge,
    OtpChallengeStatus,
    OtpLockout,
    SecurityEventKind,
    User,
)
from apps.notifications.services import OtpSendFailed, send_otp
from core.api_errors import ApiError
from core.keys import KeyRing, parse_key_ring
from core.phones import InvalidPhone, normalize_phone
from core.ratelimit import Limit, RateLimitUnavailable, hit
from core.redis import get_redis

log = structlog.get_logger(__name__)

_CODE_PURPOSE = "otp-code"
_OPEN = (OtpChallengeStatus.PENDING, OtpChallengeStatus.ACTIVE)


def invalid_code() -> ApiError:
    return ApiError("invalid_code", "The code is not valid. Request a new one if needed.")


def locked(retry_after: int) -> ApiError:
    return ApiError(
        "locked",
        "Too many wrong codes. Try again later.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after=retry_after,
    )


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        "rate_limited",
        "Too many requests. Try again later.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after=retry_after,
    )


def service_unavailable() -> ApiError:
    return ApiError(
        "service_unavailable",
        "The code could not be sent right now. Try again later.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@cache
def _ring(raw: str) -> KeyRing:
    return parse_key_ring("OTP_PEPPER_KEYS", raw)


def _pepper() -> KeyRing:
    return _ring(settings.OTP_PEPPER_KEYS)


def _code_message(challenge_id: uuid.UUID, code: str) -> bytes:
    return f"{challenge_id}:{code}".encode()


def _seconds_until(moment: datetime, now: datetime) -> int:
    return max(1, int((moment - now).total_seconds() + 0.999))


def _phone(raw: str) -> str:
    try:
        return normalize_phone(raw)
    except InvalidPhone as exc:
        raise ApiError("invalid_phone", "The phone number is not valid.", reason=exc.code) from exc


def _number_locked_until(phone: str, now: datetime) -> datetime | None:
    until = OtpLockout.objects.filter(phone=phone).values_list("locked_until", flat=True).first()
    return until if until is not None and until > now else None


# ---------------------------------------------------------------- request


@dataclass(frozen=True)
class OtpRequested:
    challenge_id: uuid.UUID
    channel: str
    expires_in: int
    resend_after: int


def _check_rate_limits(phone: str, ip: str | None, device_id: str | None) -> None:
    """Per number (primary), per IP (loose: carrier NAT), per device. Redis down: fail closed."""
    checks = [(Limit("otp-number", *settings.OTP_LIMIT_PER_NUMBER), phone)]
    if ip:
        checks.append((Limit("otp-ip", *settings.OTP_LIMIT_PER_IP), ip))
    if device_id:
        checks.append((Limit("otp-device", *settings.OTP_LIMIT_PER_DEVICE), device_id))
    try:
        verdicts = [hit(limit, identifier) for limit, identifier in checks]
    except RateLimitUnavailable:
        log.error("otp_rate_limit_store_unavailable")
        raise service_unavailable() from None
    refused = [v.retry_after for v in verdicts if not v.allowed]
    if refused:
        raise rate_limited(max(refused))


def _nairobi_midnights(now: datetime) -> tuple[datetime, datetime]:
    local = timezone.localtime(now, ZoneInfo(settings.BUSINESS_TIME_ZONE))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _check_daily_cap(phone: str, now: datetime) -> None:
    """at most OTP_DAILY_CAP_PER_NUMBER codes per number per Nairobi day."""
    start, next_start = _nairobi_midnights(now)
    sent_today = OtpChallenge.objects.filter(phone=phone, created_at__gte=start).count()
    if sent_today < settings.OTP_DAILY_CAP_PER_NUMBER:
        return
    if _first_today(f"otp-daily-cap-alerted:{start.date()}", phone):
        log.error("otp_daily_cap_reached", cap=settings.OTP_DAILY_CAP_PER_NUMBER)
        record_security_event(
            SecurityEventKind.OTP_DAILY_CAP,
            phone_e164=phone,
            metadata={"threshold": min(settings.OTP_DAILY_CAP_PER_NUMBER, MAX_METADATA_INT)},
        )
    raise rate_limited(_seconds_until(next_start, now))


def _first_today(name: str, subject: str) -> bool:
    """True the first time ``name`` is set for ``subject`` today (so an alert fires once)."""
    key = f"flag:{name}:{_pepper().sign('flag', subject.encode())}"
    try:
        return bool(get_redis().set(key, 1, nx=True, ex=2 * 86400))
    except redis.RedisError:
        return True  # better a repeated alert than a missed one


def _count_send(phone: str, channel: str, now: datetime) -> int:
    """Cost of the accepted send, and the all-numbers daily alert (OTP pumping)."""
    cost = int(settings.OTP_ESTIMATED_COST_MINOR.get(channel, 0))
    start, _ = _nairobi_midnights(now)
    try:
        sends = int(get_redis().incr(f"otp-sends:{start.date()}"))
        get_redis().expire(f"otp-sends:{start.date()}", 2 * 86400)
    except redis.RedisError:
        sends = 0
    threshold = settings.OTP_DAILY_SEND_ALERT_THRESHOLD
    if sends > threshold and _first_today(f"otp-cost-alerted:{start.date()}", "all"):
        log.error("otp_daily_send_threshold_exceeded", threshold=threshold)
        record_security_event(
            SecurityEventKind.OTP_COST_ALERT,
            metadata={"threshold": min(threshold, MAX_METADATA_INT)},
        )
    log.info("otp_sent", channel=channel, cost_minor=cost)
    return cost


def request_otp(
    raw_phone: str, *, ip: str | None = None, device_id: str | None = None
) -> OtpRequested:
    """Create a challenge for the number and send its code (WhatsApp, then SMS once).

    Checks, in order: lockout, rate limits (Redis; fail closed), daily cap, resend cooldown.
    A refused request sends nothing and costs nothing.
    """
    phone = _phone(raw_phone)
    now = timezone.now()
    if until := _number_locked_until(phone, now):
        raise locked(_seconds_until(until, now))
    _check_rate_limits(phone, ip, device_id)
    _check_daily_cap(phone, now)
    cooldown = timedelta(seconds=settings.OTP_RESEND_AFTER_SECONDS)
    last = (
        OtpChallenge.objects.filter(phone=phone)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last is not None and last + cooldown > now:
        raise rate_limited(_seconds_until(last + cooldown, now))

    code = f"{secrets.randbelow(10**settings.OTP_LENGTH):0{settings.OTP_LENGTH}d}"
    challenge_id = uuid.uuid4()
    with transaction.atomic():
        OtpChallenge.objects.filter(phone=phone, status__in=_OPEN).update(
            status=OtpChallengeStatus.SUPERSEDED
        )
        OtpChallenge.objects.create(
            id=challenge_id,
            phone=phone,
            code_hash=_pepper().sign(_CODE_PURPOSE, _code_message(challenge_id, code)),
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        )

    try:  # outside any transaction ( rule 2)
        sent = send_otp(phone, code)
    except OtpSendFailed as exc:
        OtpChallenge.objects.filter(id=challenge_id).update(status=OtpChallengeStatus.SEND_FAILED)
        for failure in exc.failures:
            _record_send_failure(phone, failure.channel.value, failure.reason)
        raise service_unavailable() from None
    OtpChallenge.objects.filter(id=challenge_id, status=OtpChallengeStatus.PENDING).update(
        status=OtpChallengeStatus.ACTIVE,
        channel=sent.channel.value,
        cost_minor=_count_send(phone, sent.channel.value, now),
    )
    for failure in sent.failures:
        _record_send_failure(phone, failure.channel.value, failure.reason)
    return OtpRequested(
        challenge_id=challenge_id,
        channel=sent.channel.value,
        expires_in=settings.OTP_TTL_SECONDS,
        resend_after=settings.OTP_RESEND_AFTER_SECONDS,
    )


def _record_send_failure(phone: str, channel: str, reason: str) -> None:
    record_security_event(
        SecurityEventKind.OTP_SEND_FAILED,
        phone_e164=phone,
        metadata={"channel": channel, "reason": reason},
    )


# ---------------------------------------------------------------- verify


@dataclass(frozen=True)
class VerifiedPhone:
    phone: str
    user_id: uuid.UUID
    is_new_user: bool


def _parse_challenge_id(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def verify_otp(challenge_id: object, code: object) -> VerifiedPhone:
    """Check ``code`` against the challenge. Returns the verified phone's user (created if new).

    A wrong code counts an attempt; the last allowed wrong attempt locks the challenge and the
    number (escalating, ). The attempt is committed before the error is raised.
    """
    parsed = _parse_challenge_id(challenge_id)
    if parsed is None:
        raise invalid_code()
    outcome, phone, retry_after = _check_code(parsed, str(code))
    if outcome == "locked":
        raise locked(retry_after)
    if outcome != "ok":
        raise invalid_code()
    user, created = _user_for(phone)
    return VerifiedPhone(phone=phone, user_id=user.id, is_new_user=created)


def _check_code(challenge_id: uuid.UUID, code: str) -> tuple[str, str, int]:
    now = timezone.now()
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(id=challenge_id).first()
        if challenge is None:
            return "invalid", "", 0
        if until := _number_locked_until(challenge.phone, now):
            return "locked", challenge.phone, _seconds_until(until, now)
        if challenge.status != OtpChallengeStatus.ACTIVE or challenge.expires_at <= now:
            return "invalid", challenge.phone, 0
        message = _code_message(challenge.id, code)
        if _pepper().verify(_CODE_PURPOSE, message, challenge.code_hash):
            challenge.status, challenge.used_at = OtpChallengeStatus.USED, now
            challenge.save(update_fields=["status", "used_at"])
            return "ok", challenge.phone, 0
        challenge.attempts += 1
        record_security_event(
            SecurityEventKind.OTP_VERIFY_FAILED,
            phone_e164=challenge.phone,
            metadata={"attempts": challenge.attempts},
        )
        if challenge.attempts < settings.OTP_MAX_ATTEMPTS:
            challenge.save(update_fields=["attempts"])
            return "invalid", challenge.phone, 0
        challenge.status = OtpChallengeStatus.LOCKED
        challenge.save(update_fields=["attempts", "status"])
        until = _lock_number(challenge.phone, now)
        return "locked", challenge.phone, _seconds_until(until, now)


def _lock_number(phone: str, now: datetime) -> datetime:
    lockout, _ = OtpLockout.objects.select_for_update().get_or_create(phone=phone)
    reset = timedelta(seconds=settings.OTP_LOCKOUT_RESET_SECONDS)
    if lockout.last_locked_at is not None and now - lockout.last_locked_at > reset:
        lockout.level = 0
    steps = settings.OTP_LOCKOUT_STEPS_SECONDS
    duration = steps[min(lockout.level, len(steps) - 1)]
    lockout.level += 1
    lockout.locked_until = now + timedelta(seconds=duration)
    lockout.last_locked_at = now
    lockout.save()
    record_security_event(SecurityEventKind.OTP_CHALLENGE_LOCKED, phone_e164=phone)
    record_security_event(
        SecurityEventKind.NUMBER_LOCKED, phone_e164=phone, metadata={"level": lockout.level}
    )
    return lockout.locked_until


def _user_for(phone: str) -> tuple[User, bool]:
    with transaction.atomic():
        user = User.objects.filter(phone=phone).first()
        if user is not None:
            return user, False
        return User.objects.create_user(phone), True
