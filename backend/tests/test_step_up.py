"""The step-up policy, step-up OTP and PIN endpoints, over HTTP.

Probes in tests/chama_probe_urls.py stand in for the sensitive endpoints of later milestones;
each is guarded by ``RequiresAction.for_(action)`` and goes through the real middleware.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import OtpChallenge, User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.notifications import fake
from apps.organizations.models import Chama, Membership
from apps.organizations.permissions import Action
from apps.organizations.roles import Role
from apps.organizations.stepup import STEP_UP
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"
PIN = "275931"


@pytest.fixture(autouse=True)
def probe_urls(settings, fake_senders):
    settings.ROOT_URLCONF = "tests.chama_probe_urls"


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)  # 09:00 Nairobi
        monkeypatch.setattr(timezone, "now", lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _later(clock: Clock, pair: TokenPair, seconds: float) -> TokenPair:
    """Move the clock and refresh, as the app does (a refresh never renews step-up)."""
    clock.advance(seconds)
    return sessions.refresh(pair.refresh_token)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    return Clock(monkeypatch)


def _owner(phone: str = PHONE, role: Role = Role.CHAIR) -> tuple[User, Chama]:
    user = User.objects.create_user(phone)
    chama = Chama.objects.create()
    with chama_context(chama.id):
        Membership.objects.create(chama=chama, user=user, role=role)
    return user, chama


def _login(user: User, chama: Chama, installation: str = "install-0001") -> TokenPair:
    return sessions.login(user.id, DeviceInfo(installation, "android"), chama.id)


def _h(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


def _post(client: Client, url: str, pair: TokenPair, body: dict[str, Any] | None = None) -> Any:
    return client.post(url, body or {}, content_type="application/json", headers=_h(pair))


def _step_up_otp(client: Client, pair: TokenPair, phone: str = PHONE) -> Any:
    sent = _post(client, "/api/v1/auth/step-up/otp/request", pair)
    assert sent.status_code == 202, sent.content
    code = fake.outbox(phone)[0]["code"]
    return _post(
        client,
        "/api/v1/auth/step-up/otp/verify",
        pair,
        {"challenge_id": sent.json()["challenge_id"], "code": code},
    )


def _set_pin(client: Client, pair: TokenPair) -> None:
    assert _step_up_otp(client, pair).status_code == 200
    response = client.put(
        "/api/v1/auth/pin", {"pin": PIN}, content_type="application/json", headers=_h(pair)
    )
    assert response.status_code == 204, response.content


def _action(client: Client, pair: TokenPair, action: Action) -> Any:
    return _post(client, f"/probe/action/{action.value}", pair)


# ---------------------------------------------------------------- the policy table


def test_step_up_table():
    assert STEP_UP == {
        Action.RECORD_RECEIPT: ("pin",),
        Action.EDIT_CONSTITUTION: ("pin",),
        Action.MANAGE_MEMBERS: ("otp",),
        Action.CHANGE_ROLES: ("otp",),
        Action.EXPORT_DATA: ("otp", "pin"),
        Action.PAYMENT_LINKING: ("otp",),
    }


@pytest.mark.parametrize("action", sorted(STEP_UP))
def test_role_05_step_up_action_without_fresh_factors_is_refused(client, clock, action):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)
    pair = _later(clock, pair, 16 * 60)  # both factors stale
    response = _action(client, pair, action)
    assert response.status_code == 403
    error = response.json()["error"]
    assert (error["code"], error["factors"]) == ("step_up_required", list(STEP_UP[action]))


@pytest.mark.parametrize("action", sorted(STEP_UP))
def test_step_up_action_with_fresh_factors_is_allowed(client, clock, action):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)  # fresh OTP and, by setting it, a fresh PIN
    assert _action(client, pair, action).status_code == 200


@pytest.mark.parametrize("action", [a for a in Action if a not in STEP_UP])
def test_actions_without_step_up_need_only_the_role(client, action):
    user, chama = _owner()
    response = _action(client, _login(user, chama), action)
    assert response.status_code == 200


def test_pin_action_without_a_pin_is_pin_not_set(client):
    user, chama = _owner()
    response = _action(client, _login(user, chama), Action.RECORD_RECEIPT)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "pin_not_set")


def test_role_is_checked_before_step_up(client):
    user, chama = _owner(role=Role.MEMBER)
    pair = _login(user, chama)
    _set_pin(client, pair)
    response = _action(client, pair, Action.MANAGE_MEMBERS)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "permission_denied")


def test_pin_is_fresh_for_15_minutes_and_otp_for_5(client, clock):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)
    clock.advance(5 * 60 + 1)
    assert _action(client, pair, Action.RECORD_RECEIPT).status_code == 200  # PIN still fresh
    assert _action(client, pair, Action.MANAGE_MEMBERS).json()["error"]["factors"] == ["otp"]
    pair = _later(clock, pair, 10 * 60)
    assert _action(client, pair, Action.RECORD_RECEIPT).json()["error"]["factors"] == ["pin"]
    verified = _post(client, "/api/v1/auth/step-up/pin", pair, {"pin": PIN})
    assert verified.json() == {"factor": "pin", "fresh_for": 900}
    assert _action(client, pair, Action.RECORD_RECEIPT).status_code == 200


@pytest.mark.parametrize("action", [a for a, f in STEP_UP.items() if "otp" in f])
def test_login_never_counts_as_a_step_up_otp(client, action):
    user, chama = _owner()
    pair = _login(user, chama)
    response = client.put(  # right after login: the first PIN needs no second code ...
        "/api/v1/auth/pin", {"pin": PIN}, content_type="application/json", headers=_h(pair)
    )
    assert response.status_code == 204
    error = _action(client, pair, action).json()["error"]  # ... but every OTP action does
    assert (error["code"], error["factors"]) == ("step_up_required", ["otp"])


def test_freshness_belongs_to_one_session(client):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)
    other = _login(user, chama, "install-0002")
    response = _action(client, other, Action.RECORD_RECEIPT)
    assert response.json()["error"]["factors"] == ["pin"]


# ---------------------------------------------------------------- step-up OTP


def test_step_up_otp_goes_to_the_callers_own_number(client):
    user, chama = _owner()
    pair = _login(user, chama)
    response = _step_up_otp(client, pair)
    assert response.json() == {"factor": "otp", "fresh_for": 300}
    assert sessions.authenticate_access(pair.access_token).step_up_otp_at is not None


def test_step_up_otp_shares_the_login_limits_and_counters(client):
    user, chama = _owner()
    pair = _login(user, chama)
    first = client.post(
        "/api/v1/auth/otp/request", {"phone": PHONE}, content_type="application/json"
    )
    assert first.status_code == 202
    again = _post(client, "/api/v1/auth/step-up/otp/request", pair)
    assert (again.status_code, again.json()["error"]["code"]) == (429, "rate_limited")
    assert OtpChallenge.objects.filter(phone=PHONE).count() == 1  # one counter for both


def test_a_code_for_another_number_does_not_step_up(client):
    user, chama = _owner()
    pair = _login(user, chama)
    before = sessions.authenticate_access(pair.access_token).step_up_otp_at  # the login's
    other_phone = "+254712000999"
    sent = client.post(
        "/api/v1/auth/otp/request", {"phone": other_phone}, content_type="application/json"
    )
    code = fake.outbox(other_phone)[0]["code"]
    response = _post(
        client,
        "/api/v1/auth/step-up/otp/verify",
        pair,
        {"challenge_id": sent.json()["challenge_id"], "code": code},
    )
    assert response.json()["error"]["code"] == "invalid_code"
    assert sessions.authenticate_access(pair.access_token).step_up_otp_at == before


def test_wrong_step_up_code_is_invalid_code(client):
    user, chama = _owner()
    pair = _login(user, chama)
    sent = _post(client, "/api/v1/auth/step-up/otp/request", pair)
    response = _post(
        client,
        "/api/v1/auth/step-up/otp/verify",
        pair,
        {"challenge_id": sent.json()["challenge_id"], "code": "000000"},
    )
    assert response.json()["error"]["code"] == "invalid_code"


# ---------------------------------------------------------------- PIN endpoints


def test_first_pin_over_http_needs_a_fresh_otp(client, clock):
    user, chama = _owner()
    pair = _later(clock, _login(user, chama), 301)  # the login's OTP is stale
    response = client.put(
        "/api/v1/auth/pin", {"pin": PIN}, content_type="application/json", headers=_h(pair)
    )
    assert response.json()["error"] == {
        "code": "step_up_required",
        "message": "Confirm it is you to continue.",
        "factors": ["otp"],
    }
    _set_pin(client, pair)
    assert client.get("/api/v1/me", headers=_h(pair)).json()["has_pin"] is True
    again = client.put(
        "/api/v1/auth/pin", {"pin": "802461"}, content_type="application/json", headers=_h(pair)
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "pin_already_set")


def test_weak_pin_over_http(client):
    user, chama = _owner()
    pair = _login(user, chama)
    _step_up_otp(client, pair)
    response = client.put(
        "/api/v1/auth/pin", {"pin": "123456"}, content_type="application/json", headers=_h(pair)
    )
    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "sequence"


def test_change_pin_over_http(client):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)
    wrong = _post(
        client,
        "/api/v1/auth/pin/change",
        pair,
        {"current_pin": "802461", "new_pin": "802462"},
    )
    assert wrong.json()["error"] == {
        "code": "invalid_pin",
        "message": "The PIN is not correct.",
        "attempts_left": 4,
    }
    ok = _post(client, "/api/v1/auth/pin/change", pair, {"current_pin": PIN, "new_pin": "802461"})
    assert ok.status_code == 204


def test_pin_lock_over_http(client):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)
    statuses = [
        _post(client, "/api/v1/auth/step-up/pin", pair, {"pin": "802461"}).status_code
        for _ in range(5)
    ]
    assert statuses == [400, 400, 400, 400, 429]
    locked = _post(client, "/api/v1/auth/step-up/pin", pair, {"pin": PIN}).json()["error"]
    assert locked["code"] == "pin_locked" and locked["retry_after"] > 0


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("put", "/api/v1/auth/pin"),
        ("post", "/api/v1/auth/pin/change"),
        ("post", "/api/v1/auth/step-up/pin"),
        ("post", "/api/v1/auth/step-up/otp/request"),
        ("post", "/api/v1/auth/step-up/otp/verify"),
    ],
)
def test_step_up_endpoints_need_a_session(client, method, url):
    response = getattr(client, method)(url, {}, content_type="application/json")
    assert response.status_code == 401


def test_put_pin_replaces_a_pin_whose_pepper_key_is_missing(client, settings):
    user, chama = _owner()
    pair = _login(user, chama)
    _set_pin(client, pair)
    settings.PIN_PEPPER_KEYS = "k9:" + "Q" * 43  # the key that peppered it left the ring
    assert client.get("/api/v1/me", headers=_h(pair)).json()["has_pin"] is False
    assert _action(client, pair, Action.RECORD_RECEIPT).json()["error"]["code"] == "pin_not_set"
    response = client.put(  # the step-up OTP from _set_pin is still fresh
        "/api/v1/auth/pin", {"pin": "802461"}, content_type="application/json", headers=_h(pair)
    )
    assert response.status_code == 204, response.content
    assert client.get("/api/v1/me", headers=_h(pair)).json()["has_pin"] is True
    assert _action(client, pair, Action.RECORD_RECEIPT).status_code == 200
