""": tests (like the app and workers) connect as app_rw, a role that can read and write rows
but cannot create, own, or escape anything. If any of these fail, RLS tests prove nothing."""

import pytest
from django.db import DatabaseError, connection, transaction

pytestmark = pytest.mark.django_db

APP_ROLE = "app_rw"
OWNER_ROLE = "app_owner"


def _one(sql: str, params: tuple[str, ...] = ()) -> object:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def _fails(sql: str) -> DatabaseError:
    """Run a statement that must be refused, inside a savepoint so the test transaction survives."""
    with pytest.raises(DatabaseError) as excinfo, transaction.atomic(), connection.cursor() as c:
        c.execute(sql)
    return excinfo.value


def test_tests_connect_as_app_rw_not_owner_or_superuser():
    assert _one("SELECT current_user") == APP_ROLE
    assert _one("SELECT session_user") == APP_ROLE


@pytest.mark.parametrize("role", [APP_ROLE, OWNER_ROLE])
def test_role_attributes(role: str):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolreplication, rolcreatedb "
            "FROM pg_roles WHERE rolname = %s",
            [role],
        )
        superuser, bypassrls, createrole, replication, createdb = cursor.fetchone()
    assert (superuser, bypassrls, createrole, replication) == (False, False, False, False)
    # app_owner may create databases only so the test runner can build the test database.
    assert createdb is (role == OWNER_ROLE)


def test_app_rw_is_member_of_no_role():
    assert (
        _one(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles u ON u.oid = m.member "
            "WHERE u.rolname = %s",
            (APP_ROLE,),
        )
        == 0
    )


def test_app_rw_owns_nothing():
    owned = _one(
        """
        SELECT (SELECT count(*) FROM pg_class WHERE relowner = r.oid)
             + (SELECT count(*) FROM pg_namespace WHERE nspowner = r.oid)
             + (SELECT count(*) FROM pg_proc WHERE proowner = r.oid)
             + (SELECT count(*) FROM pg_type WHERE typowner = r.oid)
             + (SELECT count(*) FROM pg_database WHERE datdba = r.oid)
        FROM pg_roles r WHERE r.rolname = %s
        """,
        (APP_ROLE,),
    )
    assert owned == 0


def test_every_table_is_owned_by_app_owner():
    others = _one(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner <> %s",
        (OWNER_ROLE,),
    )
    assert others == 0


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE rogue (id int)",
        "CREATE TEMP TABLE rogue (id int)",
        "CREATE VIEW rogue AS SELECT 1",
        "CREATE FUNCTION rogue() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE SCHEMA rogue",
    ],
)
def test_app_rw_cannot_create_objects(statement: str):
    assert "permission denied" in str(_fails(statement))


def test_app_rw_cannot_set_role_to_owner():
    assert "permission denied" in str(_fails(f"SET ROLE {OWNER_ROLE}"))


def test_app_rw_cannot_alter_or_drop_tables():
    assert "must be owner" in str(_fails("ALTER TABLE django_content_type ADD COLUMN x int"))
    assert "must be owner" in str(_fails("DROP TABLE django_content_type"))


RLS_TABLE = "rls_probe_proberecord"


def test_app_rw_cannot_disable_or_unforce_row_level_security():
    assert "must be owner" in str(_fails(f"ALTER TABLE {RLS_TABLE} DISABLE ROW LEVEL SECURITY"))
    assert "must be owner" in str(_fails(f"ALTER TABLE {RLS_TABLE} NO FORCE ROW LEVEL SECURITY"))
    assert "must be owner" in str(_fails(f"DROP POLICY chama_isolation ON {RLS_TABLE}"))


def test_row_security_off_errors_instead_of_bypassing():
    """With row_security=off, a role subject to RLS gets an error, never unfiltered rows."""
    with pytest.raises(DatabaseError) as excinfo, transaction.atomic(), connection.cursor() as c:
        c.execute("SET LOCAL row_security = off")
        c.execute("SELECT count(*) FROM rls_probe_proberecord")
    assert "row-level security" in str(excinfo.value)


def test_app_rw_cannot_truncate():
    assert "permission denied" in str(_fails("TRUNCATE django_content_type"))


def test_app_rw_can_read_but_not_write_migration_history():
    assert _one("SELECT count(*) FROM django_migrations") != 0
    assert "permission denied" in str(_fails("DELETE FROM django_migrations"))
