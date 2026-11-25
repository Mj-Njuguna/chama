"""One-time codes: AUTH-01, AUTH-02, AUTH-03, AUTH-06, late delivery, and the same
behaviour for registered and unregistered numbers."""

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts import audit, otp
from apps.accounts.models import OtpChallenge, OtpChallengeStatus, OtpLockout, User
from apps.notifications.services import OtpChannel
from core.api_errors import ApiError

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"
URL = "/api/v1/auth/otp/request"


@pytest.fixture
def events(monkeypatch):
    """Captures security events (rows without chama or user are invisible to app_rw)."""
    recorded: list[tuple[str, dict[str, Any]]] = []
    real = audit.record_security_event

    def spy(kind, **kwargs):
        recorded.append((str(kind), kwargs.get("metadata") or {}))
        return real(kind, **kwargs)

    monkeypatch.setattr("apps.accounts.otp.record_security_event", spy)
    return recorded


def _code(fake, phone: str = PHONE) -> str:
    return str(fake.outbox(phone)[0]["code"])


def _age_challenges(phone: str = PHONE, seconds: int = 61) -> None:
    OtpChallenge.objects.filter(phone=phone).update(
        created_at=timezone.now() - timedelta(seconds=seconds)
    )


def _error(call) -> ApiError:
    with pytest.raises(ApiError) as info:
        call()
    return info.value


# ---------------------------------------------------------------- request


def test_request_sends_a_code_and_stores_only_its_hash(fake_senders):
    sent = otp.request_otp("0712 345 678")
    code = _code(fake_senders)
    assert sent.channel == "whatsapp" and sent.expires_in == 300 and sent.resend_after == 60
    challenge = OtpChallenge.objects.get(id=sent.challenge_id)
    assert challenge.status == OtpChallengeStatus.ACTIVE and challenge.phone == PHONE
    # AUTH-06: only a keyed hash, with its key ID; the code appears in no column.
    assert challenge.code_hash.startswith("dev1:") and code not in challenge.code_hash
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_to_json(c)::text FROM accounts_otpchallenge c")
        assert code not in cursor.fetchone()[0]


def test_request_endpoint(client: Client, fake_senders):
    response = client.post(URL, {"phone": "0712345678"}, content_type="application/json")
    assert response.status_code == 202
    assert set(response.json()) == {"challenge_id", "channel", "expires_in", "resend_after"}


@pytest.mark.parametrize("phone", ["0201234567", "nope", "+447700900123"])
def test_invalid_numbers_are_refused_with_a_reason(client: Client, fake_senders, phone: str):
    response = client.post(URL, {"phone": phone}, content_type="application/json")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_phone" and error["reason"]
    assert fake_senders.outbox(PHONE) == []


def test_resend_waits_for_the_cooldown(fake_senders):
    otp.request_otp(PHONE)
    error = _error(lambda: otp.request_otp(PHONE))
    assert error.status_code == 429 and error.error_code == "rate_limited"
    assert 1 <= error.extra["retry_after"] <= 60
    _age_challenges()
    otp.request_otp(PHONE)
    assert len(fake_senders.outbox(PHONE)) == 2


def test_a_new_code_supersedes_the_old_one(fake_senders):
    first = otp.request_otp(PHONE)
    first_code = _code(fake_senders)
    _age_challenges()
    otp.request_otp(PHONE)
    assert _error(lambda: otp.verify_otp(first.challenge_id, first_code)).error_code == (
        "invalid_code"
    )


def test_failed_send_is_generic_and_its_late_code_is_useless(fake_senders, events):
    for channel in OtpChannel:
        fake_senders.BEHAVIOUR[(channel, PHONE)] = "failed"
    error = _error(lambda: otp.request_otp(PHONE))
    assert error.status_code == 503 and error.error_code == "service_unavailable"
    challenge = OtpChallenge.objects.get()
    assert challenge.status == OtpChallengeStatus.SEND_FAILED
    assert [k for k, _ in events] == ["otp_send_failed", "otp_send_failed"]
    # A provider that timed out might still deliver: the code must not work.
    fake_senders.BEHAVIOUR.clear()
    real_code = "000000"
    assert _error(lambda: otp.verify_otp(challenge.id, real_code)).error_code == "invalid_code"


def test_sms_fallback_is_recorded(fake_senders, events):
    fake_senders.BEHAVIOUR[(OtpChannel.WHATSAPP, PHONE)] = "undeliverable"
    assert otp.request_otp(PHONE).channel == "sms"
    assert events == [("otp_send_failed", {"channel": "whatsapp", "reason": "undeliverable"})]


# ---------------------------------------------------------------- verify


def test_verify_creates_the_user_once(fake_senders):
    sent = otp.request_otp(PHONE)
    verified = otp.verify_otp(sent.challenge_id, _code(fake_senders))
    assert verified.is_new_user and verified.phone == PHONE
    assert User.objects.get().id == verified.user_id
    _age_challenges()
    again = otp.request_otp(PHONE)
    second = otp.verify_otp(again.challenge_id, _code(fake_senders))
    assert not second.is_new_user and second.user_id == verified.user_id


def test_auth_02_a_code_works_once(fake_senders):
    sent = otp.request_otp(PHONE)
    code = _code(fake_senders)
    otp.verify_otp(sent.challenge_id, code)
    assert _error(lambda: otp.verify_otp(sent.challenge_id, code)).error_code == "invalid_code"


def test_auth_03_expired_codes_are_rejected(fake_senders):
    sent = otp.request_otp(PHONE)
    OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    error = _error(lambda: otp.verify_otp(sent.challenge_id, _code(fake_senders)))
    assert error.error_code == "invalid_code"
    assert not User.objects.exists()


@pytest.mark.parametrize("challenge_id", ["not-a-uuid", "00000000-0000-4000-8000-000000000000"])
def test_unknown_challenges_are_invalid_codes(challenge_id):
    assert _error(lambda: otp.verify_otp(challenge_id, "123456")).error_code == "invalid_code"


def test_auth_01_five_wrong_codes_lock_the_challenge_and_the_number(fake_senders, events):
    sent = otp.request_otp(PHONE)
    code = _code(fake_senders)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert _error(lambda: otp.verify_otp(sent.challenge_id, wrong)).error_code == (
            "invalid_code"
        )
    error = _error(lambda: otp.verify_otp(sent.challenge_id, wrong))
    assert error.error_code == "locked" and error.status_code == 429
    assert 899 <= error.extra["retry_after"] <= 900
    assert OtpChallenge.objects.get().status == OtpChallengeStatus.LOCKED
    # The right code no longer works, and the number cannot get a new one.
    assert _error(lambda: otp.verify_otp(sent.challenge_id, code)).error_code == "locked"
    _age_challenges()
    assert _error(lambda: otp.request_otp(PHONE)).error_code == "locked"
    kinds = [k for k, _ in events]
    assert kinds.count("otp_verify_failed") == 5
    assert kinds[-2:] == ["otp_challenge_locked", "number_locked"]
    assert events[-1][1] == {"level": 1}


def _lock_once(fake) -> int:
    OtpLockout.objects.update(locked_until=None)  # the previous lockout has run out
    _age_challenges()
    sent = otp.request_otp(PHONE)
    wrong = "000000" if _code(fake) != "000000" else "111111"
    for _ in range(4):
        _error(lambda: otp.verify_otp(sent.challenge_id, wrong))
    return int(_error(lambda: otp.verify_otp(sent.challenge_id, wrong)).extra["retry_after"])


# Many requests for one number: lift the request limits, which are tested in test_otp_limits.
@override_settings(OTP_LIMIT_PER_NUMBER=(100, 900), OTP_DAILY_CAP_PER_NUMBER=100)
def test_auth_01_lockout_escalates_and_resets_after_a_quiet_period(fake_senders):
    assert [_lock_once(fake_senders) for _ in range(4)] == [900, 3600, 86400, 86400]
    OtpLockout.objects.update(last_locked_at=timezone.now() - timedelta(days=2))
    assert _lock_once(fake_senders) == 900


@override_settings(OTP_MAX_ATTEMPTS=2)
def test_attempt_limit_is_a_setting(fake_senders):
    sent = otp.request_otp(PHONE)
    _error(lambda: otp.verify_otp(sent.challenge_id, "x"))
    assert _error(lambda: otp.verify_otp(sent.challenge_id, "x")).error_code == "locked"


# ---------------------------------------------------------------- registered or not: identical


def _shape(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: (type(v).__name__ if k == "challenge_id" else v) for k, v in raw.items()}


def test_request_is_identical_for_registered_and_unregistered_numbers(client: Client, fake_senders):
    registered, unregistered = "+254712000001", "+254712000002"
    User.objects.create_user(registered)
    bodies, statuses = [], []
    for phone in (registered, unregistered):
        response = client.post(URL, {"phone": phone}, content_type="application/json")
        statuses.append(response.status_code)
        bodies.append(_shape(response.json()))
    assert statuses == [202, 202] and bodies[0] == bodies[1]
    for phone in (registered, unregistered):  # the cooldown answers the same way too
        response = client.post(URL, {"phone": phone}, content_type="application/json")
        assert response.status_code == 429 and response.json()["error"]["code"] == "rate_limited"
    # Same work for both: the request path never looks at users.
    counts = []
    for phone in (registered, unregistered):
        _age_challenges(phone)
        with CaptureQueriesContext(connection) as captured:
            otp.request_otp(phone)
        counts.append(len(captured.captured_queries))
        assert not any("accounts_user" in q["sql"] for q in captured.captured_queries)
    assert counts[0] == counts[1]
