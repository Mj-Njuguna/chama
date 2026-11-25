""": contrib.auth is installed for AUTH_USER_MODEL only. Its permission and group tables
stay empty: access is decided by Membership.role, never by Django permissions or groups."""

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.models.signals import post_migrate

from apps.accounts.apps import CREATE_PERMISSIONS_UID

pytestmark = pytest.mark.django_db

AUTH_TABLES = ("auth_permission", "auth_group", "auth_group_permissions")


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_auth_tables_hold_no_rows(table: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT count(*) FROM "{table}"')  # noqa: S608  fixed table names
        assert cursor.fetchone() == (0,)


def test_permissions_are_not_created_after_migrate() -> None:
    uids = {lookup_key[0] for lookup_key, *_ in post_migrate.receivers}
    assert CREATE_PERMISSIONS_UID not in uids


def test_migrate_leaves_auth_tables_empty() -> None:
    # Re-running migrate fires post_migrate, which is when contrib.auth would add permissions.
    call_command("migrate", verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM auth_permission")
        assert cursor.fetchone() == (0,)


def test_migrations_are_in_sync_with_models() -> None:
    # The membership role and status CHECKs are generated from the choices: a new role without a
    # migration would be refused by the database.
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
