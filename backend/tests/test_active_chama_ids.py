"""active_chama_ids(): IDs only, no context needed, locked-down definition."""

from typing import Any

import pytest
from django.db import connection

from apps.organizations.models import Chama
from core.tenancy import active_chama_ids, current_chama_id

pytestmark = pytest.mark.django_db


def _function_row() -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner),
                   pg_get_function_result(p.oid), p.provolatile, p.proacl::text[]
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = 'active_chama_ids'
            """
        )
        rows = cursor.fetchall()
    assert len(rows) == 1, "exactly one active_chama_ids() (no overloads)"
    return tuple(rows[0])


def test_returns_only_active_chama_ids_without_any_context():
    active = Chama.objects.create()
    also_active = Chama.objects.create()
    Chama.objects.create(status=Chama.Status.SUSPENDED)
    assert current_chama_id() is None
    assert active_chama_ids() == sorted([active.id, also_active.id])


def test_definition_is_locked_down():
    secdef, config, owner, result, volatility, acl = _function_row()
    assert secdef is True
    assert config == ["search_path=pg_catalog, pg_temp"]
    assert owner == "app_owner"
    assert result == "SETOF uuid"
    assert volatility == "s"  # STABLE
    # Exactly two ACL entries: the owner's own, and EXECUTE (X) for app_rw granted by app_owner.
    # A PUBLIC grant would appear as "=X/app_owner".
    assert sorted(acl) == ["app_owner=X/app_owner", "app_rw=X/app_owner"]


def test_owner_is_not_a_superuser_and_cannot_bypass_rls():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT r.rolsuper, r.rolbypassrls FROM pg_proc p JOIN pg_roles r "
            "ON r.oid = p.proowner WHERE p.oid = 'public.active_chama_ids()'::regprocedure"
        )
        assert cursor.fetchone() == (False, False)


@pytest.mark.parametrize(("role", "allowed"), [("public", False), ("app_rw", True)])
def test_execute_privilege(role: str, allowed: bool):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege(%s, 'public.active_chama_ids()', 'EXECUTE')", [role]
        )
        assert cursor.fetchone() == (allowed,)
