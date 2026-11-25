"""OTP request limits: AUTH-04 (number, IP, device), AUTH-05 (daily cap), the cost
counter and alert, Redis down, client IP behind proxies, cleanup, and identical behaviour for
registered and unregistered numbers (review item 2)."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone

from apps.accounts import audit, otp
from apps.accounts.models import OtpChallenge, OtpLockout, User
from apps.accounts.tasks import purge_otp_records
from core.api_errors import ApiError
from core.client_ip import client_ip

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/otp/request"
PHONE = "+254712345678"


@pytest.fixture
def events(monkeypatch):
    recorded: list[tuple[str, dict[str, Any]]] = []
    real = audit.record_security_event

    def spy(kind, **kwargs):
        recorded.append((str(kind), kwargs.get("metadata") or {}))
        return real(kind, **kwargs)

    monkeypatch.setattr("apps.accounts.otp.record_security_event", spy)
    return recorded


def _post(client: Client, phone: str, ip: str = "203.0.113.1", device: str | None = None):
    body = {"phone": phone} | ({"device_id": device} if device else {})
    return client.post(URL, body, content_type="application/json", REMOTE_ADDR=ip)


def _age(phone: str | None = None, seconds: int = 61) -> None:
    rows = OtpChallenge.objects.filter(phone=phone) if phone else OtpChallenge.objects.all()
    rows.update(created_at=timezone.now() - timedelta(seconds=seconds))


def _error(response) -> dict[str, Any]:
    return dict(response.json()["error"])


# ---------------------------------------------------------------- AUTH-04


def test_auth_04_per_number_limit(client, fake_senders):
    for _ in range(3):
        assert _post(client, PHONE).status_code == 202
        _age(PHONE)
    response = _post(client, PHONE)
    assert response.status_code == 429 and _error(response)["code"] == "rate_limited"
    assert 1 <= _error(response)["retry_after"] <= 900
    assert len(fake_senders.outbox(PHONE)) == 3  # nothing sent for the refused request
    assert OtpChallenge.objects.count() == 3  # and nothing counted toward cost


@override_settings(OTP_LIMIT_PER_IP=(4, 3600))
def test_auth_04_per_ip_limit(client, fake_senders):
    phones = [f"+2547120000{i:02d}" for i in range(5)]
    statuses = [_post(client, p, ip="198.51.100.9").status_code for p in phones]
    assert statuses == [202, 202, 202, 202, 429]
    assert fake_senders.outbox(phones[-1]) == []
    assert _post(client, "+254712000099", ip="198.51.100.10").status_code == 202  # other IP


def test_per_ip_default_is_loose_for_carrier_nat(settings):
    hits, window = settings.OTP_LIMIT_PER_IP
    assert 30 <= hits <= 60 and window == 3600  # review item 1
    assert settings.OTP_LIMIT_PER_NUMBER[0] < hits  # the number limit is the primary control


@override_settings(OTP_LIMIT_PER_DEVICE=(2, 3600))
def test_auth_04_per_device_limit(client, fake_senders):
    device = "install-abc12345"
    statuses = [
        _post(client, f"+2547120001{i:02d}", ip=f"203.0.113.{i}", device=device).status_code
        for i in range(3)
    ]
    assert statuses == [202, 202, 429]


def test_bad_device_ids_are_validation_errors(client, fake_senders):
    response = _post(client, PHONE, device="no spaces allowed!")
    assert response.status_code == 400 and _error(response)["code"] == "validation_error"


# ---------------------------------------------------------------- AUTH-05


@override_settings(OTP_LIMIT_PER_NUMBER=(100, 900), OTP_DAILY_CAP_PER_NUMBER=3)
def test_auth_05_daily_cap_blocks_until_nairobi_midnight_and_alerts(client, fake_senders, events):
    for _ in range(3):
        assert _post(client, PHONE).status_code == 202
        _age(PHONE)
    first, second = _post(client, PHONE), _post(client, PHONE)
    assert first.status_code == second.status_code == 429
    assert _error(first)["code"] == "rate_limited"
    assert 1 <= _error(first)["retry_after"] <= 86400
    assert [k for k, _ in events].count("otp_daily_cap") == 1  # alert raised once
    assert len(fake_senders.outbox(PHONE)) == 3
    # Yesterday's codes do not count.
    _age(PHONE, seconds=2 * 86400)
    assert _post(client, PHONE).status_code == 202


# ---------------------------------------------------------------- cost counter and alert


@override_settings(OTP_ESTIMATED_COST_MINOR={"whatsapp": 120, "sms": 70})
def test_accepted_sends_record_their_estimated_cost(fake_senders):
    sent = otp.request_otp(PHONE)
    assert OtpChallenge.objects.get(id=sent.challenge_id).cost_minor == 120


@override_settings(OTP_DAILY_SEND_ALERT_THRESHOLD=2)
def test_daily_send_alert_fires_once_above_the_threshold(client, fake_senders, events):
    for i in range(5):
        assert _post(client, f"+2547120002{i:02d}", ip=f"203.0.113.{i}").status_code == 202
    alerts = [m for k, m in events if k == "otp_cost_alert"]
    assert alerts == [{"threshold": 2}]


# ---------------------------------------------------------------- Redis down


def test_redis_down_fails_closed_and_verify_still_works(client, fake_senders, monkeypatch):
    sent = otp.request_otp(PHONE)
    code = fake_senders.outbox(PHONE)[0]["code"]

    class Down:
        def pipeline(self):
            import redis

            raise redis.ConnectionError("down")

    monkeypatch.setattr("core.ratelimit.get_redis", lambda: Down())
    _age(PHONE)
    response = _post(client, PHONE)
    assert response.status_code == 503 and _error(response)["code"] == "service_unavailable"
    assert len(fake_senders.outbox(PHONE)) == 1  # nothing sent
    assert otp.verify_otp(sent.challenge_id, code).phone == PHONE  # verification unaffected


# ---------------------------------------------------------------- client IP


@pytest.mark.parametrize(
    ("proxies", "remote", "forwarded", "expected"),
    [
        (0, "10.0.0.1", "1.2.3.4", "10.0.0.1"),  # no proxy: header ignored
        (1, "10.0.0.1", "6.6.6.6, 1.2.3.4", "1.2.3.4"),  # client-forged left part ignored
        (2, "10.0.0.1", "6.6.6.6, 1.2.3.4, 10.0.0.2", "1.2.3.4"),
        (1, "10.0.0.1", "", "10.0.0.1"),  # header missing
        (1, "10.0.0.1", "not-an-ip", "10.0.0.1"),
    ],
)
def test_client_ip_trusts_exactly_the_configured_proxies(
    settings, proxies, remote, forwarded, expected
):
    settings.TRUSTED_PROXY_COUNT = proxies
    request = RequestFactory().get("/", REMOTE_ADDR=remote, HTTP_X_FORWARDED_FOR=forwarded)
    assert client_ip(request) == expected


# ---------------------------------------------------------------- cleanup


def test_old_challenges_and_quiet_lockouts_are_purged(fake_senders):
    otp.request_otp(PHONE)
    _age(PHONE, seconds=49 * 3600)
    otp.request_otp("+254722000111")
    now = timezone.now()
    OtpLockout.objects.create(phone=PHONE, level=1, last_locked_at=now - timedelta(days=2))
    OtpLockout.objects.create(
        phone="+254722000111", level=1, locked_until=now + timedelta(hours=1), last_locked_at=now
    )
    assert purge_otp_records() == {"challenges": 1, "lockouts": 1}
    assert list(OtpLockout.objects.values_list("phone", flat=True)) == ["+254722000111"]


# ---------------------------------------------------------------- registered or not (item 2)


def _shape(response) -> tuple[int, dict[str, Any]]:
    body = response.json()
    if "error" in body:
        return response.status_code, body
    return response.status_code, {k: type(v).__name__ for k, v in body.items()}


REGISTERED, UNREGISTERED = "+254712000301", "+254712000302"


def _both(client, step) -> list[tuple[int, dict[str, Any]]]:
    return [_shape(step(client, phone)) for phone in (REGISTERED, UNREGISTERED)]


def _drop_retry(
    shapes: list[tuple[int, dict[str, Any]]],
) -> list[tuple[int, dict[str, Any]]]:
    """retry_after depends on the clock; compare it as present-and-positive."""
    out = []
    for status, body in shapes:
        error = body.get("error")
        if error and "retry_after" in error:
            assert error["retry_after"] > 0
            body = {"error": {**error, "retry_after": "n"}}
        out.append((status, body))
    return out


def test_every_limit_answers_the_same_for_registered_and_unregistered(client, fake_senders):
    User.objects.create_user(REGISTERED)

    def ok(c, p):
        return _post(c, p, ip=f"203.0.113.{p[-1]}")

    # First request, cooldown, number limit.
    shapes = _both(client, ok)
    assert shapes[0] == shapes[1] and shapes[0][0] == 202
    assert _drop_retry(_both(client, ok)) == [(429, _retry("rate_limited"))] * 2  # cooldown
    for _ in range(2):
        _age()
        _both(client, ok)
    _age()
    assert _drop_retry(_both(client, ok)) == [(429, _retry("rate_limited"))] * 2  # number limit


def test_lockout_answers_the_same_for_registered_and_unregistered(client, fake_senders):
    User.objects.create_user(REGISTERED)
    answers: list[tuple[Any, ...]] = []
    for phone in (REGISTERED, UNREGISTERED):
        sent = otp.request_otp(phone)
        for _ in range(5):
            with pytest.raises(ApiError) as info:
                otp.verify_otp(sent.challenge_id, "x")
        answers.append((info.value.error_code, info.value.status_code, info.value.extra))
        _age(phone)
        with pytest.raises(ApiError) as again:
            otp.request_otp(phone)
        answers.append((again.value.error_code, again.value.status_code))
    assert answers[0] == answers[2] and answers[1] == answers[3]
    assert answers[0][0] == "locked" and answers[1] == ("locked", 429)


@override_settings(OTP_LIMIT_PER_NUMBER=(100, 900), OTP_DAILY_CAP_PER_NUMBER=1)
def test_daily_cap_answers_the_same_for_registered_and_unregistered(client, fake_senders):
    User.objects.create_user(REGISTERED)

    def ok(c, p):
        return _post(c, p, ip=f"203.0.113.{p[-1]}")

    _both(client, ok)
    _age()
    assert _drop_retry(_both(client, ok)) == [(429, _retry("rate_limited"))] * 2


def _retry(code: str) -> dict[str, Any]:
    messages = {"rate_limited": "Too many requests. Try again later."}
    return {"error": {"code": code, "message": messages[code], "retry_after": "n"}}
