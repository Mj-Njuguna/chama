"""Catalog test: every table in the public schema is either under the standard chama
RLS policy or on the named allowlist in ``core.tenancy.RLS_EXEMPT_TABLES`` with a reason.

It reads the real PostgreSQL catalog after all migrations, so a new table that forgets
``chama_rls(...)``, forgets ``chama_id``, uses a hand-written or extra policy, or is a view
cannot slip through.
"""

from dataclasses import dataclass

import pytest
from django.db import connection

from core.tenancy import (
    AUDIT_CHAMA_READ_POLICY,
    AUDIT_INSERT_POLICY,
    AUDIT_POLICY_ROLE,
    AUDIT_TABLES,
    AUDIT_USER_READ_POLICY,
    POLICY_NAME,
    RLS_EXEMPT_TABLES,
    USER_ISOLATION_POLICY,
    USER_POLICY_NAME,
    USER_POLICY_ROLE,
    USER_READ_TABLES,
    USER_RLS_TABLES,
    append_only_audit_sql,
    enable_rls_sql,
    user_isolation_sql,
    user_self_read_sql,
)

pytestmark = pytest.mark.django_db

# POLICY_PREDICATE exactly as PostgreSQL 16 stores (deparses) it in pg_policies.
STORED_PREDICATE = (
    "(chama_id = (NULLIF(current_setting('app.current_chama'::text, true), ''::text))::uuid)"
)
# USER_POLICY_PREDICATE as stored: read-only, own rows, only without chama context.
STORED_USER_PREDICATE = (
    "((NULLIF(current_setting('app.current_chama'::text, true), ''::text) IS NULL) AND "
    "(user_id = (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid))"
)

# The three audit policies as stored.
STORED_AUDIT_INSERT_CHECK = f"((chama_id IS NULL) OR {STORED_PREDICATE})"
STORED_AUDIT_USER_READ = (
    "((chama_id IS NULL) AND "
    "(NULLIF(current_setting('app.current_chama'::text, true), ''::text) IS NULL) AND "
    "(user_id = (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid))"
)

# user_isolation as stored: per-user tables without chama_id.
STORED_USER_ISOLATION = (
    "(user_id = (NULLIF(current_setting('app.current_user_id'::text, true), ''::text))::uuid)"
)


@dataclass(frozen=True)
class Relation:
    name: str
    kind: str
    has_chama_id: bool
    rls_enabled: bool
    rls_forced: bool


@dataclass(frozen=True)
class Policy:
    name: str
    permissive: str
    roles: tuple[str, ...]
    cmd: str
    qual: str | None
    with_check: str | None


def _relations() -> list[Relation]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, c.relkind,
                   EXISTS (SELECT 1 FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attname = 'chama_id'
                             AND NOT a.attisdropped),
                   c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
            ORDER BY c.relname
            """
        )
        return [Relation(*row) for row in cursor.fetchall()]


def _policies(table: str) -> list[Policy]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT policyname, permissive, roles::text[], cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'public' AND tablename = %s",
            [table],
        )
        return [Policy(n, p, tuple(r), c, q, w) for n, p, r, c, q, w in cursor.fetchall()]


def rls_problems(relation: Relation) -> list[str]:
    """Why ``relation`` is not correctly isolated; empty if it is."""
    if relation.name in RLS_EXEMPT_TABLES:
        return []
    if relation.kind not in ("r", "p"):
        return [f"{relation.name}: views/foreign tables need an allowlist entry and a reason"]
    problems = []
    if relation.name in USER_RLS_TABLES:
        return _user_rls_problems(relation)
    if not relation.has_chama_id:
        problems.append(f"{relation.name}: no chama_id column and not on the RLS allowlist")
    if not relation.rls_enabled:
        problems.append(f"{relation.name}: row level security not enabled")
    if not relation.rls_forced:
        problems.append(f"{relation.name}: row level security not forced")
    expected = [
        Policy(POLICY_NAME, "PERMISSIVE", ("public",), "ALL", STORED_PREDICATE, STORED_PREDICATE)
    ]
    if relation.name in AUDIT_TABLES:
        role, perm = (AUDIT_POLICY_ROLE,), "PERMISSIVE"
        expected = [
            Policy(AUDIT_CHAMA_READ_POLICY, perm, role, "SELECT", STORED_PREDICATE, None),
            Policy(AUDIT_INSERT_POLICY, perm, role, "INSERT", None, STORED_AUDIT_INSERT_CHECK),
            Policy(AUDIT_USER_READ_POLICY, perm, role, "SELECT", STORED_AUDIT_USER_READ, None),
        ]
    elif relation.name in USER_READ_TABLES:
        expected.append(
            Policy(
                USER_POLICY_NAME,
                "PERMISSIVE",
                (USER_POLICY_ROLE,),
                "SELECT",
                STORED_USER_PREDICATE,
                None,
            )
        )
    policies = sorted(_policies(relation.name), key=lambda p: p.name)
    if policies != expected:
        # Any other permissive policy would OR in and widen access, so "exactly these" matters.
        problems.append(f"{relation.name}: policies {policies} != {expected}")
    return problems


def _user_rls_problems(relation: Relation) -> list[str]:
    problems = []
    if relation.has_chama_id:
        problems.append(f"{relation.name}: has chama_id, so it belongs under chama RLS")
    if not (relation.rls_enabled and relation.rls_forced):
        problems.append(f"{relation.name}: row level security not enabled and forced")
    expected = [
        Policy(
            USER_ISOLATION_POLICY,
            "PERMISSIVE",
            ("app_rw",),
            "ALL",
            STORED_USER_ISOLATION,
            STORED_USER_ISOLATION,
        )
    ]
    if _policies(relation.name) != expected:
        problems.append(f"{relation.name}: policies {_policies(relation.name)} != {expected}")
    return problems


def test_every_table_is_under_chama_rls_or_allowlisted():
    problems = [p for relation in _relations() for p in rls_problems(relation)]
    assert problems == [], "\n".join(problems)


def test_allowlist_has_no_stale_entries():
    existing = {relation.name for relation in _relations()}
    assert sorted(set(RLS_EXEMPT_TABLES) - existing) == []


def test_every_allowlist_entry_has_a_reason():
    for table, reason in RLS_EXEMPT_TABLES.items():
        assert len(reason.split()) >= 5, f"{table}: give a real reason"


def test_probe_table_is_actually_checked():
    """Guards against the check passing vacuously (e.g. a query that matches nothing)."""
    names = {relation.name for relation in _relations()}
    assert "rls_probe_proberecord" in names
    assert "rls_probe_proberecord" not in RLS_EXEMPT_TABLES


@pytest.mark.parametrize("bad", ["Invoices", "x; DROP TABLE y", "public.x", "", "a" * 64])
def test_generator_rejects_non_plain_table_names(bad: str):
    with pytest.raises(ValueError, match="table name"):
        enable_rls_sql(bad)


def test_user_read_tables_exist_are_under_rls_and_have_reasons():
    relations = {r.name: r for r in _relations()}
    for table, reason in USER_READ_TABLES.items():
        assert table in relations, f"stale USER_READ_TABLES entry: {table}"
        assert table not in RLS_EXEMPT_TABLES, f"{table}: user policy on an RLS-exempt table"
        assert len(reason.split()) >= 5, f"{table}: give a real reason"


def test_user_policy_generator_refuses_unlisted_tables():
    with pytest.raises(ValueError, match="USER_READ_TABLES"):
        user_self_read_sql("rls_probe_proberecord")


# ---------------------------------------------------------------- composite foreign keys


def _chama_fk_problems() -> list[str]:
    """Foreign keys between two chama_id tables that do not pair chama_id with chama_id.

    A foreign key check bypasses RLS, so a plain ``(x_id) REFERENCES t (id)`` would accept a row
    of another chama, so such keys are composite, ``(chama_id, x_id)`` ->
    ``(chama_id, id)``.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT con.conname, src.relname, dst.relname,
                   EXISTS (
                     SELECT 1 FROM unnest(con.conkey, con.confkey) AS k(src_att, dst_att)
                     JOIN pg_attribute sa ON sa.attrelid = con.conrelid AND sa.attnum = k.src_att
                     JOIN pg_attribute da ON da.attrelid = con.confrelid AND da.attnum = k.dst_att
                     WHERE sa.attname = 'chama_id' AND da.attname = 'chama_id'
                   ) AS pairs_chama_id
            FROM pg_constraint con
            JOIN pg_class src ON src.oid = con.conrelid
            JOIN pg_class dst ON dst.oid = con.confrelid
            JOIN pg_namespace n ON n.oid = src.relnamespace
            WHERE con.contype = 'f' AND n.nspname = 'public'
              AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = con.conrelid
                          AND a.attname = 'chama_id' AND NOT a.attisdropped)
              AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = con.confrelid
                          AND a.attname = 'chama_id' AND NOT a.attisdropped)
            ORDER BY con.conname
            """
        )
        rows = cursor.fetchall()
    return [f"{name}: {src} -> {dst} lacks chama_id" for name, src, dst, ok in rows if not ok]


def test_foreign_keys_between_chama_tables_include_chama_id():
    problems = _chama_fk_problems()
    assert problems == [], "\n".join(problems)


# ---------------------------------------------------------------- append-only audit tables


def test_audit_tables_exist_are_not_exempt_and_have_reasons():
    relations = {r.name: r for r in _relations()}
    for table, reason in AUDIT_TABLES.items():
        assert table in relations, f"stale AUDIT_TABLES entry: {table}"
        assert table not in RLS_EXEMPT_TABLES and table not in USER_READ_TABLES
        assert len(reason.split()) >= 5, f"{table}: give a real reason"


def test_audit_generator_refuses_unlisted_tables():
    with pytest.raises(ValueError, match="AUDIT_TABLES"):
        append_only_audit_sql("rls_probe_proberecord")


def test_no_update_or_delete_policy_on_audit_tables():
    for table in AUDIT_TABLES:
        assert {p.cmd for p in _policies(table)} == {"SELECT", "INSERT"}


# ---------------------------------------------------------------- per-user tables


def test_user_rls_tables_exist_are_not_exempt_and_have_reasons():
    relations = {r.name for r in _relations()}
    for table, reason in USER_RLS_TABLES.items():
        assert table in relations, f"stale USER_RLS_TABLES entry: {table}"
        assert table not in RLS_EXEMPT_TABLES and table not in AUDIT_TABLES
        assert len(reason.split()) >= 5, f"{table}: give a real reason"


def test_user_isolation_generator_refuses_unlisted_tables():
    with pytest.raises(ValueError, match="USER_RLS_TABLES"):
        user_isolation_sql("rls_probe_proberecord")
