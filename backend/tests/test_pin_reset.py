"""Forgot PIN (a pending reset with cooling-off) and the new-device rules."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts import devices, sessions
from apps.accounts import pin as pins
from apps.accounts.models import SecurityEvent, StepUpPin, User
from apps.accounts.sessions import DeviceInfo
from apps.notifications import fake
from apps.organizations.models import Chama, Membership
from apps.organizations.permissions import Action
from apps.organizations.roles import Role
from apps.organizations.stepup import HIGH_RISK, STEP_UP
from core.api_errors import ApiError
from core.tenancy import chama_context, user_context

pytestmark = pytest.mark.django_db

PIN, NEW_PIN = "275931", "802461"
HOUR = 3600
COOLING = 48 * HOUR


@pytest.fixture(autouse=True)
def probe_urls(settings, redis_db):
    settings.ROOT_URLCONF = "tests.chama_probe_urls"
    settings.PIN_VERIFY_LIMIT_PER_SESSION = (1000, 900)
    fake.PUSHED.clear()
    yield
    fake.PUSHED.clear()


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)
        monkeypatch.setattr(timezone, "now", lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    return Clock(monkeypatch)


class Device:
    """One signed-in installation of the owner."""

    def __init__(self, user: User, chama: Chama, name: str, token: str = "") -> None:
        self.pair = sessions.login(user.id, DeviceInfo(name, "android"), chama.id)
        if token:
            devices.register_push_token(self.identity(), "expo", token)

    def identity(self) -> Any:
        return sessions.authenticate_access(self.pair.access_token)

    def fresh(self, *factors: str) -> "Device":
        self.pair = sessions.refresh(self.pair.refresh_token)  # the clock may have moved
        for factor in factors:
            sessions.mark_step_up(self.identity(), factor, timezone.now())
        return self

    def post(self, client: Client, url: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.pair.access_token}"}
        return client.post(url, body or {}, content_type="application/json", headers=headers)


@pytest.fixture
def owner(clock) -> tuple[User, Chama, Device]:
    user = User.objects.create_user("+254712345678")
    chama = Chama.objects.create()
    with chama_context(chama.id):
        Membership.objects.create(chama=chama, user=user, role=Role.CHAIR)
    phone = Device(user, chama, "install-0001", "ExponentPushToken[phone]")
    pins.set_first_pin(phone.fresh("otp").identity(), PIN)
    return user, chama, phone


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def _row(user: User) -> StepUpPin:
    with user_context(user.id):
        return StepUpPin.objects.get(user_id=user.id)


def _kinds(user: User) -> list[str]:
    with user_context(user.id):
        rows = SecurityEvent.objects.filter(user_id=user.id, chama_id=None)
        return list(rows.order_by("occurred_at").values_list("kind", flat=True))


def _forgot(client: Client, device: Device, pin: str = NEW_PIN) -> Any:
    return device.post(client, "/api/v1/auth/pin/forgot", {"pin": pin})


# ---------------------------------------------------------------- forgot PIN


def test_forgot_pin_needs_a_step_up_otp_and_alerts_other_devices(client, owner, clock):
    user, chama, phone = owner
    clock.advance(10 * 60)  # the setup OTP is stale; a login never counts
    tablet = Device(user, chama, "install-0002")
    assert _code(_forgot(client, tablet)) == (403, "step_up_required")
    response = _forgot(client, tablet.fresh("otp"))
    assert response.status_code == 202
    effective = datetime.fromisoformat(response.json()["effective_at"])
    assert effective == clock.now + timedelta(seconds=COOLING)
    assert [(t, m.kind) for _, t, m in fake.PUSHED] == [
        ("ExponentPushToken[phone]", "pin_reset_requested")
    ]
    assert _kinds(user)[-1] == "pin_reset_requested"
    pins.verify_pin(phone.fresh().identity(), PIN)  # the current PIN keeps working
    assert _code(phone.post(client, "/api/v1/auth/step-up/pin", {"pin": NEW_PIN})) == (
        400,
        "invalid_pin",
    )


def test_forgot_pin_never_completes_on_otp_alone(client, owner, clock):
    user, _, phone = owner
    _row_id = _row(user).pk
    with user_context(user.id):
        StepUpPin.objects.filter(user_id=user.id).update(failed_attempts=3, day_failures=3)
    _forgot(client, phone.fresh("otp"))
    early = phone.fresh("otp").post(client, "/api/v1/auth/pin/forgot/complete")
    assert _code(early) == (403, "cooling_off") and early.json()["error"]["retry_after"] > 0
    clock.advance(COOLING)
    stale = phone.fresh().post(client, "/api/v1/auth/pin/forgot/complete")
    assert _code(stale) == (403, "step_up_required")
    done = phone.fresh("otp").post(client, "/api/v1/auth/pin/forgot/complete")
    assert done.status_code == 204, done.content
    row = _row(user)
    assert row.pk == _row_id and (row.failed_attempts, row.day_failures) == (0, 3)  # cap kept
    assert row.reset_pin_hash == "" and row.reset_expires_at is None
    with pytest.raises(ApiError):
        pins.verify_pin(phone.fresh().identity(), PIN)
    pins.verify_pin(phone.identity(), NEW_PIN)
    assert _kinds(user)[-1] == "pin_reset_completed"


def test_forgot_pin_without_a_pin_is_pin_not_set(client, clock):
    user = User.objects.create_user("+254712000555")
    chama = Chama.objects.create()
    with chama_context(chama.id):
        Membership.objects.create(chama=chama, user=user, role=Role.CHAIR)
    device = Device(user, chama, "install-0009").fresh("otp")
    assert _code(_forgot(client, device)) == (403, "pin_not_set")


def test_a_weak_new_pin_and_a_second_request_are_refused(client, owner):
    _, _, phone = owner
    phone.fresh("otp")
    assert _code(_forgot(client, phone, "123456")) == (400, "pin_rejected")
    assert _forgot(client, phone).status_code == 202
    assert _code(_forgot(client, phone)) == (403, "cooling_off")


def test_any_signed_in_device_cancels_without_step_up(client, owner, clock):
    user, chama, phone = owner
    _forgot(client, phone.fresh("otp"))
    clock.advance(HOUR)
    other = Device(user, chama, "install-0002")
    assert other.post(client, "/api/v1/auth/pin/forgot/cancel").status_code == 204
    assert _kinds(user)[-1] == "pin_reset_cancelled"
    clock.advance(COOLING)
    gone = phone.fresh("otp").post(client, "/api/v1/auth/pin/forgot/complete")
    assert _code(gone) == (404, "not_found")
    assert _code(other.fresh().post(client, "/api/v1/auth/pin/forgot/cancel")) == (404, "not_found")


def test_an_unfinished_reset_expires(client, owner, clock):
    _, _, phone = owner
    _forgot(client, phone.fresh("otp"))
    clock.advance(COOLING + 7 * 24 * HOUR)
    late = phone.fresh("otp").post(client, "/api/v1/auth/pin/forgot/complete")
    assert _code(late) == (404, "not_found")


def test_high_risk_actions_wait_while_a_reset_is_open(client, owner, clock):
    _, _, phone = owner
    clock.advance(COOLING)  # past the new-device window
    _forgot(client, phone.fresh("otp"))
    phone.fresh("otp", "pin")
    for action in sorted(HIGH_RISK):
        response = phone.post(client, f"/probe/action/{action.value}")
        assert _code(response) == (403, "cooling_off"), action
    assert phone.post(client, f"/probe/action/{Action.RECORD_RECEIPT.value}").status_code == 200


def test_completing_a_reset_rechecks_the_freeze(client, owner, clock):
    user, chama, phone = owner
    _forgot(client, phone.fresh("otp"))
    clock.advance(COOLING)
    Chama.objects.filter(id=chama.id).update(frozen_at=timezone.now())
    refused = phone.fresh("otp").post(client, "/api/v1/auth/pin/forgot/complete")
    assert _code(refused) == (403, "chama_frozen")
    assert _row(user).reset_expires_at is not None  # still waiting, nothing applied


# ---------------------------------------------------------------- AUTH-14


def test_high_risk_set():
    assert frozenset({Action.PAYMENT_LINKING, Action.CHANGE_ROLES, Action.EXPORT_DATA}) == HIGH_RISK
    assert set(STEP_UP) >= HIGH_RISK


def test_auth_14_b_a_new_device_waits_48_hours_for_high_risk_actions(client, owner, clock):
    user, chama, _ = owner
    clock.advance(COOLING)  # the chama is now older than the window
    tablet = Device(user, chama, "install-0002").fresh("otp", "pin")
    for action in sorted(HIGH_RISK):
        response = tablet.post(client, f"/probe/action/{action.value}")
        assert _code(response) == (403, "cooling_off"), action
        assert response.json()["error"]["retry_after"] == COOLING
    assert tablet.post(client, f"/probe/action/{Action.RECORD_RECEIPT.value}").status_code == 200
    clock.advance(COOLING)
    tablet.fresh("otp", "pin")
    assert tablet.post(client, f"/probe/action/{Action.MANAGE_MEMBERS.value}").status_code == 200


def test_auth_14_b_chamas_younger_than_the_window_are_exempt(client, owner):
    _, _, phone = owner  # chama and device both brand new
    phone.fresh("otp", "pin")
    for action in sorted(HIGH_RISK):
        assert phone.post(client, f"/probe/action/{action.value}").status_code == 200, action


def test_auth_14_a_a_device_newer_than_an_open_change_waits(client, owner, clock):
    user, chama, phone = owner  # young chama: (b) does not apply
    created = phone.fresh("otp").post(client, "/probe/change/payment_details")
    assert created.status_code == 201
    clock.advance(HOUR)
    tablet = Device(user, chama, "install-0002").fresh("otp", "pin")
    waiting = tablet.post(client, f"/probe/action/{Action.EXPORT_DATA.value}")
    assert _code(waiting) == (403, "cooling_off")
    assert waiting.json()["error"]["retry_after"] == COOLING - HOUR  # until the change applies
    phone.fresh("otp", "pin")  # the device that existed before the request is not held
    assert phone.post(client, f"/probe/action/{Action.EXPORT_DATA.value}").status_code == 200
    change_id = created.json()["id"]
    assert phone.post(client, f"/api/v1/chama/changes/{change_id}/cancel").status_code == 200
    tablet.fresh("otp", "pin")
    assert tablet.post(client, f"/probe/action/{Action.EXPORT_DATA.value}").status_code == 200
