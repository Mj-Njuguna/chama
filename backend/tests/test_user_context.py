"""user_self_read and user_context(): a verified user reads their own memberships
across chamas before any chama is active, and nothing else."""

import re
import uuid
from dataclasses import fields
from pathlib import Path

import pytest
from django.db import ProgrammingError, connection, transaction

from apps.accounts.models import User
from apps.organizations.models import Chama, Membership
from apps.organizations.roles import Role
from apps.organizations.services import MembershipSummary, memberships_for_user
from core.tenancy import (
    ChamaContextError,
    chama_context,
    current_chama_id,
    current_user_id,
    user_context,
)

pytestmark = pytest.mark.django_db

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def world():
    """Users u and v; chamas A and B. u is owner of A and viewer of B; v is manager of A."""
    u = User.objects.create_user("0712000001")
    v = User.objects.create_user("0712000002")
    a, b = Chama.objects.create(), Chama.objects.create()
    with chama_context(a.id):
        u_in_a = Membership.objects.create(chama=a, user=u, role=Role.CHAIR)
        v_in_a = Membership.objects.create(chama=a, user=v, role=Role.TREASURER)
    with chama_context(b.id):
        u_in_b = Membership.objects.create(chama=b, user=u, role=Role.MEMBER)
    return {"u": u, "v": v, "a": a, "b": b, "u_in_a": u_in_a, "v_in_a": v_in_a, "u_in_b": u_in_b}


def _visible_ids() -> set[uuid.UUID]:
    return set(Membership.objects.values_list("id", flat=True))


# ---------------------------------------------------------------- the policy


def test_user_only_context_sees_own_memberships_across_chamas(world):
    with user_context(world["u"].id):
        assert _visible_ids() == {world["u_in_a"].id, world["u_in_b"].id}
    with user_context(world["v"].id):
        assert _visible_ids() == {world["v_in_a"].id}


def test_both_settings_show_only_the_chamas_rows(world):
    # The user's membership in B must NOT appear while chama A is active.
    with chama_context(world["a"].id), user_context(world["u"].id):
        assert current_chama_id() == world["a"].id and current_user_id() == world["u"].id
        assert _visible_ids() == {world["u_in_a"].id, world["v_in_a"].id}


def test_neither_setting_sees_nothing(world):
    assert current_chama_id() is None and current_user_id() is None
    assert _visible_ids() == set()


def test_user_only_context_cannot_insert(world):
    other = User.objects.create_user("0712000003")
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        user_context(world["u"].id),
    ):
        Membership.objects.create(chama=world["a"], user=other, role=Role.CHAIR)


def test_user_only_context_cannot_update_or_delete(world):
    with user_context(world["u"].id):
        assert Membership.objects.filter(id=world["u_in_b"].id).update(role=Role.CHAIR) == 0
        # Memberships are never deleted: app_rw has no DELETE on the table at all.
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM organizations_membership WHERE user_id = %s", [world["u"].id]
            )
    with chama_context(world["b"].id):
        assert Membership.objects.get(id=world["u_in_b"].id).role == Role.MEMBER


def test_user_context_ends_with_its_transaction(world):
    with user_context(world["u"].id):
        pass
    assert current_user_id() is None
    assert _visible_ids() == set()


def test_user_context_rejects_non_uuid_and_nested_other_user(world):
    with pytest.raises(ChamaContextError, match="UUID"), user_context("not-a-uuid"):
        pass  # pragma: no cover
    with (
        pytest.raises(ChamaContextError, match="different user"),
        user_context(world["u"].id),
        user_context(world["v"].id),
    ):
        pass  # pragma: no cover


# ---------------------------------------------------------------- the service


def test_service_returns_only_chama_role_status(world):
    assert [f.name for f in fields(MembershipSummary)] == ["chama_id", "role", "status"]
    result = memberships_for_user(world["u"].id)
    assert {(m.chama_id, m.role) for m in result} == {
        (world["a"].id, "chair"),
        (world["b"].id, "member"),
    }
    assert all(m.status == "active" for m in result)
    assert current_user_id() is None  # context did not leak


def test_service_refuses_inside_an_chama_context(world):
    with (
        pytest.raises(ChamaContextError, match="outside any chama"),
        chama_context(world["a"].id),
    ):
        memberships_for_user(world["u"].id)


def test_unknown_user_gets_nothing(world):
    assert memberships_for_user(uuid.uuid4()) == []


# ---------------------------------------------------------------- user id only from the session


def test_user_id_in_body_query_or_header_is_ignored(world, client, fake_auth):
    from tests.rls_probe import auth

    auth.USERS["tok-v"] = world["v"].id
    response = client.generic(
        "GET",
        f"/probe/memberships?user_id={world['u'].id}",
        data=f'{{"user_id": "{world["u"].id}"}}',
        content_type="application/json",
        headers={"Authorization": "Test tok-v", "X-User-Id": str(world["u"].id)},
    )
    assert response.status_code == 200
    assert response.json()["memberships"] == [[str(world["a"].id), "treasurer", "active"]]


def test_unauthenticated_request_gets_no_memberships(world, client, fake_auth):
    response = client.get(
        f"/probe/memberships?user_id={world['u'].id}", headers={"X-User-Id": str(world["u"].id)}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------- trusted call sites only

# Every production call of user_context(). Adding one is a deliberate, reviewed change:
# only the authentication middleware (after token verification) and the OTP-verify service
# (after a code is verified) may join this list.
USER_CONTEXT_CALL_SITES = {
    "apps/organizations/selectors.py": 1,  # memberships_for_user()
    "apps/accounts/sessions.py": 2,  # login() after a verified OTP; holder check
    "apps/accounts/middleware.py": 1,  # after verifying the access token
    # devices and push tokens: the verified session's user, or server-side data
    "apps/accounts/devices.py": 1,
    "apps/accounts/pin.py": 1,  # the verified session's user
}


def test_user_context_is_called_only_from_trusted_code():
    found: dict[str, int] = {}
    for top in ("apps", "core", "config"):
        for path in (BACKEND / top).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            count = len(re.findall(r"(?<!def )\buser_context\(", text))
            if count:
                found[path.relative_to(BACKEND).as_posix()] = count
    assert found == USER_CONTEXT_CALL_SITES


def test_set_config_for_identity_settings_only_in_core_tenancy():
    """Nothing but core.tenancy may write app.current_chama / app.current_user_id."""
    offenders = []
    for top in ("apps", "core", "config"):
        for path in (BACKEND / top).rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if rel == "core/tenancy.py":
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"set_config|SET\s+(LOCAL\s+)?app\.", text, re.IGNORECASE):
                offenders.append(rel)
    assert offenders == []
