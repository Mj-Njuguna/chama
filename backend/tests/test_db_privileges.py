"""Exact privileges of app_rw and PUBLIC, on existing objects and, through default
privileges, on every object app_owner creates later.

app_rw holds exactly SELECT, INSERT, UPDATE, DELETE on tables (less on READ_ONLY_TABLES, each
with a reason)
and exactly USAGE on sequences. Never TRUNCATE (bypasses RLS row filtering), REFERENCES or
TRIGGER. PUBLIC holds nothing on tables or sequences, no CREATE on schema public and no TEMP on
the database.
"""

from typing import Any

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db

TABLE_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}
# Tables where app_rw holds less than the four row privileges, each for a stated reason.
READ_ONLY_TABLES = {
    "django_migrations": {"SELECT"},  # migration history; only app_owner writes it
    "accounts_securityevent": {"SELECT", "INSERT"},  # append-only audit
    # Sessions end by revocation, never deletion.
    "accounts_authsession": {"SELECT", "INSERT", "UPDATE"},
    "accounts_authtoken": {"SELECT", "INSERT"},  # write-once retired hashes
    # The ledger is append-only: corrections are compensating transactions.
    "ledger_ledgertransaction": {"SELECT", "INSERT"},
    "ledger_ledgerentry": {"SELECT", "INSERT"},
    "ledger_receipt": {"SELECT", "INSERT"},
    # A PIN is replaced by updating its row, never deleted.
    "accounts_steppin": {"SELECT", "INSERT", "UPDATE"},
    # Chamas, memberships, constitutions and cooling-off changes are closed or revoked, never
    # deleted.
    "organizations_chama": {"SELECT", "INSERT", "UPDATE"},
    "organizations_membership": {"SELECT", "INSERT", "UPDATE"},
    "organizations_constitution": {"SELECT", "INSERT", "UPDATE"},
    "organizations_pendingchange": {"SELECT", "INSERT", "UPDATE"},
    # Allocations and obligation payments are append-only; cycles, obligations (running paid
    # totals) and waivers (approval) are updated, never deleted.
    "contributions_allocation": {"SELECT", "INSERT"},
    "contributions_obligationpayment": {"SELECT", "INSERT"},
    "contributions_cycle": {"SELECT", "INSERT", "UPDATE"},
    "contributions_obligation": {"SELECT", "INSERT", "UPDATE"},
    "contributions_finewaiver": {"SELECT", "INSERT", "UPDATE"},
    # The feed and provider refs are append-only; messages record delivery, never deleted.
    "notifications_feedevent": {"SELECT", "INSERT"},
    "notifications_providermessageref": {"SELECT", "INSERT"},
    "notifications_message": {"SELECT", "INSERT", "UPDATE"},
}
FORBIDDEN_TABLE_PRIVILEGES = ("TRUNCATE", "REFERENCES", "TRIGGER")


def _rows(sql: str, params: list[str] | None = None) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return list(cursor.fetchall())


def _granted(relkind: str, grantee: str) -> dict[str, set[str]]:
    """Privileges explicitly granted to ``grantee`` ('app_rw' or 'PUBLIC') per relation."""
    rows = _rows(
        """
        SELECT c.relname, COALESCE(
                   array_agg(acl.privilege_type) FILTER (WHERE acl.privilege_type IS NOT NULL),
                   '{}')
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN LATERAL aclexplode(c.relacl) acl
               ON acl.grantee = CASE WHEN %s = 'PUBLIC' THEN 0
                                     ELSE (SELECT oid FROM pg_roles WHERE rolname = %s) END
        WHERE n.nspname = 'public' AND c.relkind = %s
        GROUP BY c.relname
        """,
        [grantee, grantee, relkind],
    )
    return {name: set(privileges) for name, privileges in rows}


def test_there_are_tables_and_sequences_to_check():
    # Guards against vacuous passes; the probe and Django tables always exist.
    assert _granted("r", "app_rw")
    assert _granted("S", "app_rw")


def test_app_rw_has_exactly_row_privileges_on_every_table():
    wrong = {
        table: privileges
        for table, privileges in _granted("r", "app_rw").items()
        if privileges != READ_ONLY_TABLES.get(table, TABLE_PRIVILEGES)
    }
    assert wrong == {}


@pytest.mark.parametrize("privilege", FORBIDDEN_TABLE_PRIVILEGES)
def test_app_rw_has_no_truncate_references_or_trigger_anywhere(privilege: str):
    """Effective check (includes anything inherited via PUBLIC), not just explicit grants."""
    offenders = _rows(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND has_table_privilege('app_rw', c.oid, %s)
        """,
        [privilege],
    )
    assert offenders == []


def test_app_rw_has_only_usage_on_every_sequence():
    wrong = {s: p for s, p in _granted("S", "app_rw").items() if p != {"USAGE"}}
    assert wrong == {}
    effective = _rows(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S'
          AND (has_sequence_privilege('app_rw', c.oid, 'SELECT')
               OR has_sequence_privilege('app_rw', c.oid, 'UPDATE'))
        """
    )
    assert effective == []


@pytest.mark.parametrize("relkind", ["r", "S"])
def test_public_has_nothing_on_tables_or_sequences(relkind: str):
    assert {name: p for name, p in _granted(relkind, "PUBLIC").items() if p} == {}


def _default_acl(objtype: str) -> dict[str, set[str]]:
    """Default privileges app_owner grants on future objects of ``objtype`` in schema public
    (r = tables, S = sequences) or globally (f = functions), keyed by grantee."""
    rows = _rows(
        """
        SELECT CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(acl.grantee) END,
               array_agg(acl.privilege_type)
        FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) acl
        WHERE pg_get_userbyid(d.defaclrole) = 'app_owner' AND d.defaclobjtype = %s
          AND (n.nspname = 'public' OR (%s = 'f' AND d.defaclnamespace = 0))
        GROUP BY 1
        """,
        [objtype, objtype],
    )
    return {grantee: set(privileges) for grantee, privileges in rows}


def test_future_tables_get_exactly_row_privileges_for_app_rw():
    acl = _default_acl("r")
    assert acl.get("app_rw") == TABLE_PRIVILEGES
    assert "PUBLIC" not in acl


def test_future_sequences_get_only_usage_for_app_rw():
    acl = _default_acl("S")
    assert acl.get("app_rw") == {"USAGE"}
    assert "PUBLIC" not in acl


def test_future_functions_are_not_executable_by_public():
    # The global default entry lists the owner's own EXECUTE and, crucially, no PUBLIC grant.
    assert "PUBLIC" not in _default_acl("f")
    assert _default_acl("f") == {"app_owner": {"EXECUTE"}}


@pytest.mark.parametrize("role", ["public", "app_rw"])
def test_no_create_on_schema_public_and_no_temp_on_database(role: str):
    assert _rows(
        "SELECT has_schema_privilege(%s, 'public', 'CREATE'), "
        "has_database_privilege(%s, current_database(), 'TEMP'), "
        "has_database_privilege(%s, current_database(), 'CREATE')",
        [role, role, role],
    ) == [(False, False, False)]


def test_app_rw_is_not_a_member_of_app_owner():
    assert _rows("SELECT pg_has_role('app_rw', 'app_owner', 'MEMBER')") == [(False,)]


IDENTITY_TABLES = (
    "accounts_user",
    "organizations_membership",
    "organizations_chama",
    "organizations_constitution",
    "accounts_securityevent",
    "accounts_steppin",
)


@pytest.mark.parametrize("table", IDENTITY_TABLES)
def test_identity_tables_have_exactly_row_privileges(table: str):
    """Named on purpose, so a missing table cannot pass the all-tables checks above.
    Effective privileges, so anything inherited through PUBLIC counts too."""
    privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", *FORBIDDEN_TABLE_PRIVILEGES)
    held = {
        p
        for p in privileges
        if _rows("SELECT has_table_privilege('app_rw', %s, %s)", [table, p])[0][0]
    }
    assert held == READ_ONLY_TABLES.get(table, TABLE_PRIVILEGES)


# Column-level UPDATE grants. Every other column of these tables is fixed once written.
COLUMN_UPDATES: dict[str, set[str]] = {}


def test_app_rw_has_exactly_the_column_update_grants():
    rows = _rows(
        """
        SELECT c.relname, a.attname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        JOIN LATERAL aclexplode(a.attacl) acl ON true
        WHERE n.nspname = 'public' AND acl.privilege_type = 'UPDATE'
          AND acl.grantee = (SELECT oid FROM pg_roles WHERE rolname = 'app_rw')
        """
    )
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    assert found == COLUMN_UPDATES
