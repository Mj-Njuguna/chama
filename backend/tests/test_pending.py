"""Cooling-off chama changes and the "this wasn't me" freeze, over HTTP.

The payment-linking endpoints arrive with the payments module; probes in
tests/chama_probe_urls.py request and apply changes through the real policy and module.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.utils import timezone

from apps.accounts import devices, sessions
from apps.accounts.models import SecurityEvent, User
from apps.accounts.sessions import DeviceInfo
from apps.notifications import fake
from apps.organizations.models import Chama, Membership, PendingChange, PendingChangeStatus
from apps.organizations.permissions import Action
from apps.organizations.roles import Role
from apps.organizations.stepup import STEP_UP
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

COOLING = 48 * 3600
EXPIRY = 7 * 24 * 3600


@pytest.fixture(autouse=True)
def probe_urls(settings, redis_db):
    settings.ROOT_URLCONF = "tests.chama_probe_urls"
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


class Member:
    def __init__(self, chama: Chama, phone: str, role: Role, token: str = "") -> None:
        self.user = User.objects.create_user(phone)
        self.chama = chama
        with chama_context(chama.id):
            Membership.objects.create(chama=chama, user=self.user, role=role)
        device = DeviceInfo(f"inst-{phone[-4:]}", "android")
        self.pair = sessions.login(self.user.id, device, chama.id)
        if token:
            devices.register_push_token(self.identity(), "expo", token)

    def identity(self) -> Any:
        return sessions.authenticate_access(self.pair.access_token)

    def fresh_otp(self) -> "Member":
        sessions.mark_step_up(self.identity(), "otp", timezone.now())
        return self

    def later(self) -> "Member":
        """Refresh after the clock moved (the access token lives 15 minutes)."""
        self.pair = sessions.refresh(self.pair.refresh_token)
        return self

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.pair.access_token}"}


@pytest.fixture
def team(clock: Clock) -> dict[str, Member]:
    """Created on the test clock (device and chama ages matter for AUTH-14)."""
    chama = Chama.objects.create()
    return {
        "chair": Member(chama, "+254712000001", Role.CHAIR, "ExponentPushToken[owner]"),
        "owner2": Member(chama, "+254712000002", Role.CHAIR, "ExponentPushToken[owner2]"),
        "treasurer": Member(chama, "+254712000003", Role.MEMBER, "ExponentPushToken[manager]"),
    }


def _post(client: Client, url: str, member: Member) -> Any:
    return client.post(url, {}, content_type="application/json", headers=member.headers())


def _request(client: Client, member: Member, kind: str = "payment_details") -> Any:
    return _post(client, f"/probe/change/{kind}", member)


def _apply(client: Client, member: Member, change_id: str, kind: str = "payment_details") -> Any:
    return _post(client, f"/probe/change/{kind}/{change_id}/apply", member)


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def _events(chama: Chama) -> list[tuple[str, dict[str, Any]]]:
    with chama_context(chama.id):
        rows = SecurityEvent.objects.filter(chama_id=chama.id).order_by("occurred_at")
        return [(row.kind, row.metadata) for row in rows]


def _pushed() -> list[tuple[str, str]]:
    return [(token, message.kind) for _, token, message in fake.PUSHED]


# ---------------------------------------------------------------- request and apply


def test_a_change_needs_a_fresh_otp_and_alerts_every_owner(
    client, team, clock, django_capture_on_commit_callbacks
):
    owner = team["chair"]
    assert _code(_request(client, owner)) == (403, "step_up_required")  # login is no step-up
    with django_capture_on_commit_callbacks(execute=True):
        response = _request(client, owner.fresh_otp())
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "pending" and body["kind"] == "payment_details"
    effective = datetime.fromisoformat(body["effective_at"])
    assert effective == clock.now + timedelta(seconds=COOLING)
    assert datetime.fromisoformat(body["expires_at"]) == effective + timedelta(seconds=EXPIRY)
    assert sorted(_pushed()) == [
        ("ExponentPushToken[owner2]", "chama_change_requested"),
        ("ExponentPushToken[owner]", "chama_change_requested"),
    ]  # every owner, never the manager; the push carries only the change ID
    assert fake.PUSHED[0][2].data.keys() == {"change_id"}
    assert _events(owner.chama) == [("change_requested", {"reason": "payment_details"})]


def test_a_change_applies_only_after_the_cooling_off(client, team, clock):
    owner = team["chair"].fresh_otp()
    change_id = _request(client, owner).json()["id"]
    early = _apply(client, owner, change_id)
    assert _code(early) == (403, "cooling_off")
    assert early.json()["error"]["retry_after"] == COOLING
    clock.advance(COOLING)
    other = team["owner2"].later().fresh_otp()  # any owner may apply, with a fresh OTP
    response = _apply(client, other, change_id)
    assert response.status_code == 200 and response.json()["status"] == "applied"
    assert _code(_apply(client, other, change_id)) == (404, "not_found")  # applied once
    assert [kind for kind, _ in _events(owner.chama)][-1] == "change_applied"


def test_applying_needs_its_own_fresh_otp(client, team, clock):
    owner = team["chair"].fresh_otp()
    change_id = _request(client, owner).json()["id"]
    clock.advance(COOLING)
    assert _code(_apply(client, owner.later(), change_id)) == (403, "step_up_required")


def test_a_change_not_applied_in_time_expires(client, team, clock):
    owner = team["chair"].fresh_otp()
    change_id = _request(client, owner).json()["id"]
    clock.advance(COOLING + EXPIRY)
    assert _code(_apply(client, owner.later().fresh_otp(), change_id)) == (404, "not_found")
    with chama_context(owner.chama.id):
        assert PendingChange.objects.get(id=change_id).status == PendingChangeStatus.EXPIRED
    assert _request(client, owner).status_code == 201  # a new request is possible again


def test_one_open_change_per_kind(client, team):
    owner = team["chair"].fresh_otp()
    assert _request(client, owner).status_code == 201
    again = _request(client, owner)
    assert _code(again) == (403, "cooling_off")


def test_only_owners_request_changes(client, team):
    manager = team["treasurer"].fresh_otp()
    assert _code(_request(client, manager)) == (403, "permission_denied")


# ---------------------------------------------------------------- cancel


def test_any_owner_cancels_without_step_up(client, team, clock, django_capture_on_commit_callbacks):
    change_id = _request(client, team["chair"].fresh_otp()).json()["id"]
    clock.advance(3600)
    other = team["owner2"].later()  # no fresh factor at all
    listed = client.get("/api/v1/chama/changes", headers=other.headers()).json()["changes"]
    assert [c["id"] for c in listed] == [change_id]
    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, f"/api/v1/chama/changes/{change_id}/cancel", other)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert ("ExponentPushToken[owner]", "chama_change_cancelled") in _pushed()
    assert client.get("/api/v1/chama/changes", headers=other.headers()).json()["changes"] == []
    clock.advance(COOLING)
    assert _code(_apply(client, team["chair"].later().fresh_otp(), change_id)) == (404, "not_found")


def test_managers_cannot_see_or_cancel_changes(client, team):
    change_id = _request(client, team["chair"].fresh_otp()).json()["id"]
    manager = team["treasurer"]
    assert client.get("/api/v1/chama/changes", headers=manager.headers()).status_code == 403
    cancel = _post(client, f"/api/v1/chama/changes/{change_id}/cancel", manager)
    assert _code(cancel) == (403, "permission_denied")


def test_another_chamas_change_is_not_found(client, team):
    change_id = _request(client, team["chair"].fresh_otp()).json()["id"]
    stranger = Member(Chama.objects.create(), "+254712000009", Role.CHAIR)
    cancel = _post(client, f"/api/v1/chama/changes/{change_id}/cancel", stranger)
    assert _code(cancel) == (404, "not_found")


# ---------------------------------------------------------------- freeze


def test_this_wasnt_me_freezes_cancels_and_alerts(
    client, team, clock, django_capture_on_commit_callbacks
):
    change_id = _request(client, team["chair"].fresh_otp()).json()["id"]
    other = team["owner2"]  # any owner, no step-up
    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, "/api/v1/chama/freeze", other)
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["frozen_at"]) == clock.now
    assert sorted(t for t, kind in _pushed() if kind == "chama_frozen") == [
        "ExponentPushToken[owner2]",
        "ExponentPushToken[owner]",
    ]
    with chama_context(other.chama.id):
        assert PendingChange.objects.get(id=change_id).status == PendingChangeStatus.CANCELLED
    assert _events(other.chama)[-1] == ("chama_frozen", {"count": 1})
    clock.advance(60)
    again = _post(client, "/api/v1/chama/freeze", other)  # idempotent: same time, no event
    assert again.json()["frozen_at"] == response.json()["frozen_at"]
    assert [kind for kind, _ in _events(other.chama)].count("chama_frozen") == 1


@pytest.mark.parametrize("action", sorted(STEP_UP))
def test_a_frozen_chama_refuses_every_step_up_action(client, team, action):
    owner = team["chair"]
    sessions.mark_step_up(owner.identity(), "pin", timezone.now())  # fresh everything
    owner.fresh_otp()
    _post(client, "/api/v1/chama/freeze", owner)
    response = _post(client, f"/probe/action/{action.value}", owner)
    assert _code(response) == (403, "chama_frozen")


def test_a_frozen_chama_still_allows_reads_and_review(client, team):
    owner = team["chair"]
    _post(client, "/api/v1/chama/freeze", team["owner2"])
    totals = _post(client, f"/probe/action/{Action.VIEW_GROUP_TOTALS.value}", owner)
    assert totals.status_code == 200
    assert client.get("/api/v1/chama/changes", headers=owner.headers()).status_code == 200


def test_managers_cannot_freeze(client, team):
    assert _code(_post(client, "/api/v1/chama/freeze", team["treasurer"])) == (
        403,
        "permission_denied",
    )


def test_apply_rechecks_the_freeze_inside_its_transaction(client, team, clock, monkeypatch):
    """A freeze that lands after the permission check (a race) still wins: the chama row is
    locked and the policy runs again before the change is applied."""
    owner = team["chair"].fresh_otp()
    change_id = _request(client, owner).json()["id"]
    clock.advance(COOLING)
    owner.later().fresh_otp()
    import apps.organizations.permissions as permissions

    real_check = permissions.RequiresAction.has_permission

    def check_then_freeze(self, request, view):
        allowed = real_check(self, request, view)
        Chama.objects.filter(id=owner.chama.id).update(frozen_at=timezone.now())
        return allowed

    monkeypatch.setattr(permissions.RequiresAction, "has_permission", check_then_freeze)
    response = _apply(client, owner, change_id)
    assert _code(response) == (403, "chama_frozen")
    with chama_context(owner.chama.id):
        assert PendingChange.objects.get(id=change_id).status == PendingChangeStatus.PENDING


def test_only_an_operator_unfreezes(client, team, capsys):
    owner = team["chair"]
    _post(client, "/api/v1/chama/freeze", owner)
    call_command("unfreeze_chama", str(owner.chama.id), operator="ops_review")
    assert _events(owner.chama)[-1] == ("chama_unfrozen", {"operator": "ops_review"})
    owner.fresh_otp()
    assert _request(client, owner).status_code == 201
    with pytest.raises(CommandError, match="not frozen"):
        call_command("unfreeze_chama", str(owner.chama.id), operator="ops_review")
    with pytest.raises(CommandError, match="operator"):
        call_command("unfreeze_chama", str(owner.chama.id), operator="Ops 1")
    with pytest.raises(CommandError, match="UUID"):
        call_command("unfreeze_chama", "nope", operator="ops_review")
