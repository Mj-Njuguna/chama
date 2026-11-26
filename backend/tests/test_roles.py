"""The role x action matrix, fail-closed defaults, and the DRF permission."""

import pytest

from apps.accounts.models import User
from apps.organizations import permissions
from apps.organizations.models import Chama, Membership
from apps.organizations.permissions import ROLE_ACTIONS, WRITE_ACTIONS, Action, is_allowed
from apps.organizations.roles import Role
from core.tenancy import chama_context

CHAIR, TREAS, SEC, MEM = Role.CHAIR, Role.TREASURER, Role.SECRETARY, Role.MEMBER
OFFICIALS = {CHAIR, TREAS, SEC}

# The specification, written out independently of the implementation.
EXPECTED_ALLOWED: dict[Action, set[Role]] = {
    Action.VIEW_MEMBERS: {CHAIR, TREAS, SEC, MEM},
    Action.VIEW_CONSTITUTION: {CHAIR, TREAS, SEC, MEM},
    Action.VIEW_GROUP_TOTALS: OFFICIALS,
    Action.VIEW_SECURITY_EVENTS: OFFICIALS,
    Action.VIEW_INVITATIONS: OFFICIALS,
    Action.MANAGE_MEMBERS: OFFICIALS,
    Action.CHANGE_ROLES: {CHAIR},
    Action.EDIT_CONSTITUTION: OFFICIALS,
    Action.RECORD_RECEIPT: OFFICIALS,
    Action.WAIVE_FINE: OFFICIALS,
    Action.EXPORT_DATA: {CHAIR, TREAS},
    Action.PAYMENT_LINKING: {CHAIR, TREAS},
    Action.REVIEW_CHAMA_CHANGES: OFFICIALS,
    Action.FREEZE_CHAMA: OFFICIALS,
}


def test_spec_covers_every_action():
    assert set(EXPECTED_ALLOWED) == set(Action)


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("role", list(Role))
def test_role_01_full_matrix(role: Role, action: Action):
    assert is_allowed(role, action) is (role in EXPECTED_ALLOWED[action])


def test_every_role_action_pair_is_explicit():
    assert set(ROLE_ACTIONS) == set(Role)
    for role, row in ROLE_ACTIONS.items():
        assert set(row) == set(Action), f"{role}: every action must be written out"
        assert all(isinstance(v, bool) for v in row.values())


def test_missing_entries_default_to_deny(monkeypatch):
    trimmed = {role: dict(row) for role, row in ROLE_ACTIONS.items()}
    del trimmed[Role.CHAIR][Action.RECORD_RECEIPT]
    del trimmed[Role.TREASURER]
    monkeypatch.setattr(permissions, "ROLE_ACTIONS", trimmed)
    assert is_allowed(Role.CHAIR, Action.RECORD_RECEIPT) is False
    assert is_allowed(Role.TREASURER, Action.VIEW_GROUP_TOTALS) is False
    assert is_allowed(Role.CHAIR, Action.EXPORT_DATA) is True  # untouched entries still apply


@pytest.mark.parametrize(
    ("role", "action"),
    [("admin", Action.RECORD_RECEIPT), (Role.CHAIR, "delete_everything"), (None, None), ("", "")],
)
def test_unknown_role_or_action_is_denied(role, action):
    assert is_allowed(role, action) is False


@pytest.mark.parametrize(
    "action", [Action.EXPORT_DATA, Action.PAYMENT_LINKING, Action.CHANGE_ROLES]
)
def test_secretary_denied_money_destination_and_roles(action: Action):
    assert is_allowed(Role.SECRETARY, action) is False


@pytest.mark.parametrize("action", sorted(WRITE_ACTIONS))
def test_member_denied_every_write(action: Action):
    assert is_allowed(Role.MEMBER, action) is False


def test_write_actions_are_exactly_the_non_view_actions():
    assert {a for a in Action if not a.value.startswith("view_")} == WRITE_ACTIONS


# ---------------------------------------------------------------- RequiresAction over HTTP


@pytest.fixture
def staff(db, fake_auth):
    """Chama A with a chair and a plain member; chama B where that member is the chair."""
    from tests.rls_probe import auth

    a, b = Chama.objects.create(), Chama.objects.create()
    owner = User.objects.create_user("0712000011")
    member_a = User.objects.create_user("0712000012")
    stranger = User.objects.create_user("0712000013")
    with chama_context(a.id):
        Membership.objects.create(chama=a, user=owner, role=Role.CHAIR)
        Membership.objects.create(chama=a, user=member_a, role=Role.MEMBER)
    with chama_context(b.id):
        Membership.objects.create(chama=b, user=member_a, role=Role.CHAIR)
    for token, user, chama in [
        ("owner-a", owner, a),
        ("member_a-a", member_a, a),
        ("member_a-b", member_a, b),
        ("stranger-a", stranger, a),
    ]:
        auth.USERS[token], auth.SESSIONS[token] = user.id, chama.id
    auth.SESSIONS["chama-only"] = a.id
    auth.USERS["user-only"] = owner.id
    return {"a": a, "b": b}


def _post(client, token: str | None, **extra):
    headers = {"Authorization": f"Test {token}"} if token else {}
    return client.post(
        "/probe/chama-totals", data=extra, content_type="application/json", headers=headers
    )


@pytest.mark.parametrize(
    ("token", "status"),
    [
        ("owner-a", 200),
        ("member_a-a", 403),  # a plain member may not see group totals
        ("member_a-b", 200),  # same person, chair in chama B: role is per membership
        ("stranger-a", 403),  # session for a chama they have no membership in
        ("chama-only", 403),  # no user
        ("user-only", 404),  # no chama context: chama_not_available
        (None, 403),
    ],
)
def test_requires_action_uses_the_session_membership(client, staff, token, status):
    assert _post(client, token).status_code == status


def test_role_in_request_body_is_ignored(client, staff):
    response = _post(client, "member_a-a", role="chair", chama_id=str(staff["b"].id))
    assert response.status_code == 403


def test_revoked_membership_is_denied(client, staff):
    with chama_context(staff["a"].id):
        Membership.objects.filter(role=Role.CHAIR).update(status=Membership.Status.REVOKED)
    assert _post(client, "owner-a").status_code == 403


@pytest.mark.parametrize(("field", "value"), [("role", "admin"), ("status", "suspended")])
def test_database_refuses_unknown_role_or_status(staff, field: str, value: str):
    from django.db import IntegrityError, transaction

    match = f"organizations_membership_{field}_valid"
    stranger = User.objects.get(phone="+254712000013")
    with chama_context(staff["a"].id):
        with pytest.raises(IntegrityError, match=match), transaction.atomic():
            Membership.objects.filter(role=Role.CHAIR).update(**{field: value})
        fields = {"role": Role.MEMBER, field: value}
        with pytest.raises(IntegrityError, match=match), transaction.atomic():
            Membership.objects.create(chama=staff["a"], user=stranger, **fields)
