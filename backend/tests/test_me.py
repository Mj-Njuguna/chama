"""GET /me and PATCH /me.

chama_name on every membership, display_name, member_code and permissions (a display hint,
never authorization); PATCH /me sets only the session user's own display name.
"""

import logging
from typing import Any

import pytest
from django.test import Client

from apps.accounts import sessions
from apps.accounts.models import User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.organizations.models import Chama, Membership
from apps.organizations.permissions import ROLE_ACTIONS, Action
from apps.organizations.roles import Role
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

PHONE, OTHER_PHONE = "+254712000301", "+254712000302"


@pytest.fixture(autouse=True)
def _redis(redis_db):
    return redis_db


def _member(user: User, chama: Chama, role: Role) -> Membership:
    with chama_context(chama.id):
        return Membership.objects.create(chama=chama, user=user, role=role)


def _login(user: User, chama: Chama | None) -> TokenPair:
    device = DeviceInfo("install-me-01", "android")
    return sessions.login(user.id, device, chama.id if chama else None)


def _h(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


def _me(client: Client, pair: TokenPair) -> Any:
    response = client.get("/api/v1/me", headers=_h(pair))
    assert response.status_code == 200, response.content
    return response.json()


def _patch(client: Client, pair: TokenPair, body: dict[str, Any]) -> Any:
    return client.patch("/api/v1/me", body, content_type="application/json", headers=_h(pair))


def _allowed(role: Role) -> list[str]:
    return [a.value for a, ok in ROLE_ACTIONS[role].items() if ok]


@pytest.fixture
def world():
    """A user who chairs Alpha and is a plain member of Beta."""
    user = User.objects.create_user(PHONE)
    alpha, beta = Chama.objects.create(name="Alpha Savers"), Chama.objects.create(name="Beta")
    chair = _member(user, alpha, Role.CHAIR)
    _member(user, beta, Role.MEMBER)
    return {"user": user, "alpha": alpha, "beta": beta, "chair": chair}


def test_me_has_chama_names_code_and_permissions(client, world):
    body = _me(client, _login(world["user"], world["alpha"]))
    assert body["user"]["display_name"] is None
    assert [(m["chama_id"], m["chama_name"], m["role"]) for m in body["memberships"]] == [
        (str(world["alpha"].id), "Alpha Savers", "chair"),
        (str(world["beta"].id), "Beta", "member"),
    ]
    assert body["member_code"] == world["chair"].member_code
    assert body["permissions"] == _allowed(Role.CHAIR)
    assert "manage_members" in body["permissions"]


def test_me_has_the_session_start_and_refreshes_keep_it(client, world, monkeypatch):
    """ "Last sign-in" is when the device session began with a code sign-in."""
    from datetime import datetime, timedelta

    from django.utils import timezone

    from apps.accounts.models import AuthSession

    def started_at(pair: TokenPair) -> datetime:
        return datetime.fromisoformat(_me(client, pair)["session_started_at"])

    pair = _login(world["user"], world["alpha"])
    created = AuthSession.objects.get(id=pair.session_id).created_at
    assert started_at(pair) == created
    later = timezone.now() + timedelta(minutes=20)
    monkeypatch.setattr(timezone, "now", lambda: later)
    rotated = sessions.refresh(pair.refresh_token)
    assert started_at(rotated) == created


def test_me_follows_the_active_chama(client, world):
    body = _me(client, _login(world["user"], world["beta"]))
    assert body["role"] == "member"
    assert body["permissions"] == _allowed(Role.MEMBER)
    assert "manage_members" not in body["permissions"]


def test_me_without_an_active_chama(client, world):
    body = _me(client, _login(world["user"], None))
    assert (body["role"], body["member_code"], body["permissions"]) == (None, None, [])
    assert len(body["memberships"]) == 2


def test_permissions_are_a_hint_never_authorization(client, world):
    """A stale list does not grant anything: the server checks the role on every request."""
    pair = _login(world["user"], world["alpha"])
    assert "view_security_events" in _me(client, pair)["permissions"]
    with chama_context(world["alpha"].id):
        Membership.objects.filter(user=world["user"]).update(role=Role.MEMBER)
    assert client.get("/api/v1/chama/security-events", headers=_h(pair)).status_code == 403
    assert "view_security_events" not in _me(client, pair)["permissions"]


def test_every_permission_is_a_published_action():
    for role in Role:
        assert set(_allowed(role)) <= {a.value for a in Action}


# ---------------------------------------------------------------- PATCH /me


def test_patch_me_sets_only_the_callers_display_name(client, world):
    other = User.objects.create_user(OTHER_PHONE)
    User.objects.filter(id=other.id).update(display_name="Untouched")
    pair = _login(world["user"], world["alpha"])
    response = _patch(client, pair, {"display_name": "  Mary  ", "user_id": str(other.id)})
    assert response.status_code == 200, response.content
    assert response.json()["user"]["display_name"] == "Mary"  # trimmed; the /me body
    assert User.objects.get(id=world["user"].id).display_name == "Mary"
    assert User.objects.get(id=other.id).display_name == "Untouched"


def test_patch_me_works_without_an_active_chama(client, world):
    pair = _login(world["user"], None)
    assert _patch(client, pair, {"display_name": "Mary"}).status_code == 200


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "blank"),
        ("   ", "blank"),
        ("x" * 51, "too_long"),
        ("Ma​ry", "invalid_characters"),  # zero-width space
        ("Mary‮", "invalid_characters"),  # right-to-left override
        ("Ma\nry", "invalid_characters"),
        ("Ma\x00ry", None),  # NUL: refused (by the parser or by the check)
    ],
)
def test_patch_me_refuses_bad_names(client, world, value, code):
    pair = _login(world["user"], world["alpha"])
    response = _patch(client, pair, {"display_name": value})
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "validation_error" and "display_name" in error["field_errors"]
    if code is not None:
        assert error["field_errors"]["display_name"] == [code]
    assert User.objects.get(id=world["user"].id).display_name == ""


def test_patch_me_accepts_fifty_characters_and_non_ascii(client, world):
    pair = _login(world["user"], world["alpha"])
    assert _patch(client, pair, {"display_name": "x" * 50}).status_code == 200
    body = _patch(client, pair, {"display_name": "Wanjirũ Njoroge"}).json()
    assert body["user"]["display_name"] == "Wanjirũ Njoroge"


def test_patch_me_needs_a_session(client):
    response = client.patch("/api/v1/me", {"display_name": "x"}, content_type="application/json")
    assert response.status_code == 401


def test_display_name_is_never_logged(client, world, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    pair = _login(world["user"], world["alpha"])
    _patch(client, pair, {"display_name": "Zebedayo Secretname"})
    _patch(client, pair, {"display_name": "Zebedayo​Secretname"})
    captured = capsys.readouterr()
    assert "Secretname" not in caplog.text
    assert "Secretname" not in captured.out + captured.err
