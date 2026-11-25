"""/me, POST /chamas, switch-chama, and the per-request membership check.

Everything runs over HTTP through the real middleware. Revocation is simulated the way it will
happen in production: by updating the membership row directly.
"""

import uuid
from datetime import datetime
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.accounts import sessions
from apps.accounts.models import AuthSession, User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.organizations.models import Chama, Membership
from apps.organizations.roles import Role
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"
NOT_AVAILABLE = {
    "error": {"code": "chama_not_available", "message": "This chama is not available."}
}


@pytest.fixture(autouse=True)
def probe_urls(settings):
    settings.ROOT_URLCONF = "tests.chama_probe_urls"


def _member(user: User, chama: Chama, role: Role = Role.CHAIR) -> None:
    with chama_context(chama.id):
        Membership.objects.create(chama=chama, user=user, role=role)


def _set_status(user: User, chama: Chama, status: str) -> None:
    """Revoke or restore a membership by updating its row directly."""
    with chama_context(chama.id):
        assert Membership.objects.filter(user=user).update(status=status) == 1


def _login(user: User, chama: Chama | None, installation: str = "install-0001") -> TokenPair:
    return sessions.login(user.id, DeviceInfo(installation, "android"), chama.id if chama else None)


def _headers(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


def _get(client: Client, url: str, pair: TokenPair) -> Any:
    return client.get(url, headers=_headers(pair))


def _post(client: Client, url: str, pair: TokenPair, body: dict[str, Any] | None = None) -> Any:
    return client.post(url, body or {}, content_type="application/json", headers=_headers(pair))


def _active_chama(pair: TokenPair) -> uuid.UUID | None:
    return AuthSession.objects.values_list("active_chama_id", flat=True).get(id=pair.session_id)


@pytest.fixture
def events_spy_all(monkeypatch):
    """Kinds of security events recorded by apps.organizations.chamas."""
    from apps.accounts import audit

    kinds: list[str] = []

    def spy(kind, **kwargs):
        kinds.append(str(kind))
        return audit.record_security_event(kind, **kwargs)

    monkeypatch.setattr("apps.organizations.chamas.record_security_event", spy)
    return kinds


@pytest.fixture
def world():
    """A user who chairs chama A and is the secretary of chama B, logged in on A."""
    user = User.objects.create_user(PHONE)
    a, b = Chama.objects.create(name="Alpha"), Chama.objects.create(name="Beta")
    _member(user, a, Role.CHAIR)
    _member(user, b, Role.SECRETARY)
    return {"user": user, "a": a, "b": b, "pair": _login(user, a)}


# ---------------------------------------------------------------- the per-request check


def test_active_membership_keeps_the_chama(client, world):
    pair = world["pair"]
    response = _get(client, "/probe/chama-data", pair)
    assert response.status_code == 200
    assert response.json() == {"chama": str(world["a"].id)}
    assert _get(client, "/probe/totals", pair).status_code == 200


@pytest.mark.parametrize("probe", ["/probe/chama-data", "/probe/totals"])
def test_revoked_membership_loses_the_chama_on_the_next_request(client, world, probe):
    pair = world["pair"]
    assert _get(client, probe, pair).status_code == 200
    _set_status(world["user"], world["a"], Membership.Status.REVOKED)

    response = _get(client, probe, pair)  # the same access token, no refresh in between

    assert response.status_code == 404
    assert response.json() == NOT_AVAILABLE
    assert _active_chama(pair) is None  # cleared on the session row, not just this request


def test_restoring_the_membership_does_not_silently_restore_the_session(client, world):
    pair = world["pair"]
    _set_status(world["user"], world["a"], Membership.Status.REVOKED)
    assert _get(client, "/probe/chama-data", pair).status_code == 404
    _set_status(world["user"], world["a"], Membership.Status.ACTIVE)
    assert _get(client, "/probe/chama-data", pair).status_code == 404  # must switch again
    switched = _switch(client, pair, world["a"].id)
    assert switched.status_code == 200
    assert _get(client, "/probe/chama-data", pair).status_code == 200


def test_a_session_without_an_chama_gets_chama_not_available(client, world):
    pair = _login(world["user"], None, "install-0002")
    response = _get(client, "/probe/chama-data", pair)
    assert (response.status_code, response.json()) == (404, NOT_AVAILABLE)


def test_unauthenticated_chama_endpoint_is_401_not_404(client, world):
    assert client.get("/probe/chama-data").status_code == 401


def _revoked(world) -> TokenPair:
    _set_status(world["user"], world["a"], Membership.Status.REVOKED)
    pair: TokenPair = world["pair"]
    return pair


def test_after_revocation_me_still_works(client, world):
    pair = _revoked(world)
    response = _get(client, "/api/v1/me", pair)
    assert response.status_code == 200
    body = response.json()
    assert body["active_chama_id"] is None and body["role"] is None
    assert {(m["chama_id"], m["status"]) for m in body["memberships"]} == {
        (str(world["a"].id), "revoked"),
        (str(world["b"].id), "active"),
    }
    assert _active_chama(pair) is None


def test_after_revocation_create_chama_still_works(client, world):
    pair = _revoked(world)
    response = _post(client, "/api/v1/chamas", pair, {"name": "Gamma"})
    assert response.status_code == 201
    assert _active_chama(pair) == uuid.UUID(response.json()["chama_id"])
    assert _get(client, "/probe/chama-data", pair).status_code == 200


def test_after_revocation_switch_chama_still_works(client, world):
    pair = _revoked(world)
    response = _switch(client, pair, world["b"].id)
    assert response.status_code == 200
    assert response.json() == {"active_chama_id": str(world["b"].id), "role": "secretary"}
    assert _get(client, "/probe/chama-data", pair).json() == {"chama": str(world["b"].id)}


def test_after_revocation_logout_still_works(client, world):
    pair = _revoked(world)
    assert _post(client, "/api/v1/auth/logout", pair).status_code == 204
    assert _get(client, "/api/v1/me", pair).json()["error"]["code"] == "session_revoked"


def test_after_revocation_logout_all_still_works(client, world):
    pair = _revoked(world)
    other = _login(world["user"], world["b"], "install-0002")
    assert _post(client, "/api/v1/auth/logout-all", pair).status_code == 204
    assert _get(client, "/api/v1/me", other).json()["error"]["code"] == "session_revoked"


def test_revoking_one_membership_leaves_other_sessions_on_other_chamas_alone(client, world):
    on_b = _login(world["user"], world["b"], "install-0002")
    _set_status(world["user"], world["a"], Membership.Status.REVOKED)
    assert _get(client, "/probe/chama-data", world["pair"]).status_code == 404
    assert _get(client, "/probe/chama-data", on_b).json() == {"chama": str(world["b"].id)}


# ---------------------------------------------------------------- switch-chama


def _switch(client, pair, chama_id) -> Any:
    return _post(client, "/api/v1/auth/switch-chama", pair, {"chama_id": str(chama_id)})


def test_switch_chama_binds_the_session_and_records_an_event(client, world, events_spy_all):
    pair = world["pair"]
    response = _switch(client, pair, world["b"].id)
    assert response.status_code == 200
    assert _active_chama(pair) == world["b"].id
    assert _get(client, "/api/v1/me", pair).json()["role"] == "secretary"
    assert "chama_switched" in events_spy_all


def _unavailable_cases(world) -> dict[str, uuid.UUID]:
    stranger_chama = Chama.objects.create(name="Stranger")
    revoked = Chama.objects.create(name="Revoked")
    _member(world["user"], revoked)
    _set_status(world["user"], revoked, Membership.Status.REVOKED)
    return {
        "unknown": uuid.uuid4(),
        "not_a_member": stranger_chama.id,
        "revoked": revoked.id,
    }


def test_switch_unknown_non_member_and_revoked_answer_identically(client, world):
    pair = world["pair"]
    answers, queries = {}, {}
    for case, chama_id in _unavailable_cases(world).items():
        with CaptureQueriesContext(connection) as captured:
            response = _switch(client, pair, chama_id)
        answers[case] = (response.status_code, response.json())
        queries[case] = [
            q["sql"].replace(str(chama_id), "?")
            for q in captured.captured_queries
            if "organizations_membership" in q["sql"]
        ]
    assert all(answer == (404, NOT_AVAILABLE) for answer in answers.values())
    # The same membership queries in every case: the request-time check plus one for the switch.
    assert queries["unknown"] == queries["not_a_member"] == queries["revoked"]
    assert len(queries["unknown"]) == 2
    assert _active_chama(pair) == world["a"].id  # a failed switch leaves the session alone


def test_switch_takes_the_chama_from_the_body_only_after_the_membership_check(client, world):
    pair = world["pair"]
    stranger = Chama.objects.create()
    response = client.post(
        f"/api/v1/auth/switch-chama?chama_id={world['b'].id}",
        {"chama_id": str(stranger.id)},
        content_type="application/json",
        headers={**_headers(pair), "X-Chama-Id": str(world["b"].id)},
    )
    assert response.status_code == 404
    assert _active_chama(pair) == world["a"].id


def test_switch_chama_validates_the_body(client, world):
    response = _post(client, "/api/v1/auth/switch-chama", world["pair"], {"chama_id": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# ---------------------------------------------------------------- POST /chamas

NAME = {"name": "Umoja Savers"}


def test_create_chama_makes_the_caller_chair_and_activates_it(client, events_spy_all):
    user = User.objects.create_user(PHONE)
    pair = _login(user, None)
    response = _post(client, "/api/v1/chamas", pair, {"name": "  Umoja Savers  "})
    assert response.status_code == 201
    body = response.json()
    chama_id = uuid.UUID(body["chama_id"])
    assert body == {
        "chama_id": str(chama_id),
        "name": "Umoja Savers",
        "role": "chair",
        "member_code": body["member_code"],
        "active_chama_id": str(chama_id),
    }
    assert len(body["member_code"]) == 6
    with chama_context(chama_id):
        assert list(Membership.objects.values_list("user_id", "role", "status")) == [
            (user.id, "chair", "active")
        ]
    assert _active_chama(pair) == chama_id
    assert _get(client, "/probe/totals", pair).status_code == 200
    assert "chama_created" in events_spy_all


def test_create_chama_needs_a_name(client):
    pair = _login(User.objects.create_user(PHONE), None)
    for body in ({}, {"name": "   "}):
        response = _post(client, "/api/v1/chamas", pair, body)
        assert response.status_code == 400


def test_create_chama_takes_nothing_else_from_the_body(client):
    user = User.objects.create_user(PHONE)
    other = User.objects.create_user("0712000999")
    pair = _login(user, None)
    response = _post(
        client, "/api/v1/chamas", pair, {"name": "X", "user_id": str(other.id), "role": "member"}
    )
    chama_id = uuid.UUID(response.json()["chama_id"])
    with chama_context(chama_id):
        assert list(Membership.objects.values_list("user_id", "role")) == [(user.id, "chair")]


def test_chamas_per_user_per_day_are_limited(client, settings):
    settings.CHAMAS_PER_USER_PER_DAY = 2
    pair = _login(User.objects.create_user(PHONE), None)
    statuses = [_post(client, "/api/v1/chamas", pair, NAME).status_code for _ in range(3)]
    assert statuses == [201, 201, 429]
    refused = _post(client, "/api/v1/chamas", pair, NAME).json()["error"]
    assert refused["code"] == "rate_limited"
    assert 0 < refused["retry_after"] <= 86400


def test_chamas_created_on_earlier_days_do_not_count(client, settings):
    from datetime import timedelta

    from django.utils import timezone

    settings.CHAMAS_PER_USER_PER_DAY = 1
    pair = _login(User.objects.create_user(PHONE), None)
    first = uuid.UUID(_post(client, "/api/v1/chamas", pair, NAME).json()["chama_id"])
    Chama.objects.filter(id=first).update(created_at=timezone.now() - timedelta(days=2))
    assert _post(client, "/api/v1/chamas", pair, NAME).status_code == 201


def test_the_limit_counts_only_chamas_the_user_chairs(client, settings, world):
    settings.CHAMAS_PER_USER_PER_DAY = 1
    secretary = User.objects.create_user("0712000777")
    _member(secretary, world["a"], Role.SECRETARY)  # created today, but not chaired
    pair = _login(secretary, world["a"])
    assert _post(client, "/api/v1/chamas", pair, NAME).status_code == 201


def test_create_chama_validates_the_name(client):
    pair = _login(User.objects.create_user(PHONE), None)
    response = _post(client, "/api/v1/chamas", pair, {"name": "x" * 121})
    assert response.status_code == 400


# ---------------------------------------------------------------- /me


def test_me_describes_the_caller_and_session(client, world):
    pair = world["pair"]
    body = _get(client, "/api/v1/me", pair).json()
    session = AuthSession.objects.get(id=pair.session_id)
    started = datetime.fromisoformat(body["session_started_at"])
    assert started == session.created_at
    assert body == {
        "user": {"id": str(world["user"].id), "phone": PHONE, "display_name": None},
        "session_id": str(pair.session_id),
        "session_started_at": body["session_started_at"],  # checked below
        "device_id": str(session.device_id),
        "active_chama_id": str(world["a"].id),
        "role": "chair",
        "member_code": body["member_code"],
        "memberships": [
            {
                "chama_id": str(world["a"].id),
                "chama_name": "Alpha",
                "role": "chair",
                "status": "active",
            },
            {
                "chama_id": str(world["b"].id),
                "chama_name": "Beta",
                "role": "secretary",
                "status": "active",
            },
        ],
        "has_pin": False,
        "permissions": body["permissions"],  # checked in tests/test_me.py
    }


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/v1/me"),
        ("post", "/api/v1/chamas"),
        ("post", "/api/v1/auth/switch-chama"),
    ],
)
def test_chama_endpoints_need_a_session(client, method, url):
    response = getattr(client, method)(url, content_type="application/json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
