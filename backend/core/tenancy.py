"""Tenant isolation.

Every table with an ``chama_id`` column is protected by PostgreSQL row-level security. The app
connects as ``app_rw``, which can neither bypass nor disable RLS, and every policy reads the
transaction-local setting ``app.current_chama``:

- set only by :func:`chama_context` (used by the request middleware and ``ChamaTask``),
  from the authenticated session or the task's explicit argument, never from request input;
- unset or empty means NULL, so reads return no rows and writes are rejected (fail closed).

The policy SQL is generated in exactly one place, :func:`enable_rls_sql`, and
``tests/test_rls_catalog.py`` checks that every table carries exactly that policy.

A few tables also carry a second, read-only policy keyed on ``app.current_user_id``
(:func:`user_self_read_sql`), so a verified user can list their own rows across
chamas before a chama is active. It applies only when no chama context is set.

Append-only audit tables (:func:`append_only_audit_sql`) use their own policies instead:
inserts need no chama context (events happen before login), chama rows are read in their
chama's context, rows without a chama only by their own user, and ``app_rw`` has no
``UPDATE`` or ``DELETE`` on them at all.

Trust model: these settings protect against application bugs (a forgotten filter, a view that
trusts request input). They do NOT protect against SQL injection or code that sets the wrong
value, because ``app_rw`` can call ``set_config`` with anything. That is why only
:func:`chama_context` and :func:`user_context` set them, and only trusted call sites use those.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from celery import Task
from django.db import connection, migrations, transaction

CHAMA_SETTING = "app.current_chama"
# Request attribute set ONLY by the authentication middleware, from the authenticated session.
AUTHENTICATED_CHAMA_ATTR = "authenticated_chama_id"
POLICY_NAME = "chama_isolation"
POLICY_PREDICATE = "chama_id = NULLIF(current_setting('app.current_chama', true), '')::uuid"

USER_SETTING = "app.current_user_id"
# Request attribute set ONLY by the authentication middleware, after verifying the session token.
AUTHENTICATED_USER_ATTR = "authenticated_user_id"
USER_POLICY_NAME = "user_self_read"
USER_POLICY_ROLE = "app_rw"
USER_POLICY_PREDICATE = (
    "NULLIF(current_setting('app.current_chama', true), '') IS NULL"
    " AND user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)

# Tables that carry the user_self_read policy in addition to chama_isolation. Like the RLS
# allowlist, every entry needs a reason; the catalog test refuses the policy anywhere else.
USER_READ_TABLES: dict[str, str] = {
    "organizations_membership": (
        "Login and switch-chama must list the verified user's own memberships across chamas "
        "before any chama is active. Read-only; writes need chama context."
    ),
}

# Append-only audit tables: instead of chama_isolation they carry the three audit policies and
# app_rw holds only SELECT and INSERT. Every entry needs a reason.
AUDIT_TABLES: dict[str, str] = {
    "accounts_securityevent": (
        "Security events (logins, failures, lockouts, revocations) must be written before any "
        "chama or user is known, and must never be changed or deleted by the app."
    ),
}
AUDIT_POLICY_ROLE = "app_rw"
AUDIT_INSERT_POLICY = "security_event_insert"
AUDIT_CHAMA_READ_POLICY = "security_event_chama_read"
AUDIT_USER_READ_POLICY = "security_event_user_read"
AUDIT_INSERT_CHECK = f"chama_id IS NULL OR {POLICY_PREDICATE}"
AUDIT_USER_READ_PREDICATE = f"chama_id IS NULL AND {USER_POLICY_PREDICATE}"

# Per-user tables without chama_id: under RLS keyed on app.current_user_id instead
# of the chama. Every entry needs a reason.
USER_RLS_TABLES: dict[str, str] = {
    "accounts_device": (
        "A person's phones and app installations, shared by all their chamas. Only their own "
        "user may see or change them; set by user_context after a verified OTP or token."
    ),
    "accounts_steppin": (
        "A person's server step-up PIN hash and attempt state, shared by all their "
        "chamas. Only their own user may see or change it, in user_context."
    ),
    "accounts_pushtoken": (
        "Push tokens of a person's app installations, shared by all their chamas. "
        "Only their own user may see or change them, in user_context like accounts_device."
    ),
}
USER_ISOLATION_POLICY = "user_isolation"
USER_ISOLATION_PREDICATE = (
    "user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)

# Tables in the public schema that are deliberately NOT under chama RLS. Every entry needs a
# written reason; adding one is a reviewable change to this file, checked by the catalog test.
RLS_EXEMPT_TABLES: dict[str, str] = {
    "django_migrations": "Django's migration history. No chama data; app_rw may only SELECT it.",
    "django_content_type": "Django's model registry. Global metadata, no chama data.",
    "organizations_chama": (
        "The tenant root: one row per Chama, keyed by id (it has no chama_id). A user must "
        "list the chamas they belong to before a chama is active, so it cannot be filtered "
        "by app.current_chama. Access goes through the organizations module."
    ),
    "accounts_user": (
        "People, not chama data: a user exists before any membership and may belong to several "
        "chamas. Holds no chama_id. Read only through the accounts module, keyed by "
        "the authenticated user or a membership of the current chama."
    ),
    "auth_permission": (
        "Installed with django.contrib.auth (required for AUTH_USER_MODEL). Global metadata; "
        "Django permissions are not used for access (roles live on organizations_membership)."
    ),
    "auth_group": (
        "Installed with django.contrib.auth. Unused (no PermissionsMixin); never holds data."
    ),
    "accounts_otpchallenge": (
        "One-time-code challenges exist before login, keyed by phone number: no user or chama "
        "context can exist yet. Holds only a keyed hash of the code. Read and written "
        "only by apps.accounts.otp."
    ),
    "accounts_authsession": (
        "Sessions are found by the SHA-256 hash of a presented token before the user is known, "
        "so no user or chama context can exist yet. Holds hashes only. "
        "Read and written only by apps.accounts.sessions (pinned by a test)."
    ),
    "accounts_authtoken": (
        "Hashes of retired refresh tokens, looked up before the user is known to detect reuse. "
        "Hashes only. Read and written only by apps.accounts.sessions."
    ),
    "accounts_otplockout": (
        "Escalating lockout per phone number, checked before login for every number alike. "
        "No chama data. Read and written only by apps.accounts.otp."
    ),
    "payments_callbackroute": (
        "Maps the SHA-256 hash of a chama's provider callback token to the chama. A provider "
        "callback carries no session, so the chama is found here before any chama context "
        "exists. Hashes and chama IDs only; insert-only. Read and written only by "
        "apps.payments.linking."
    ),
    "auth_group_permissions": (
        "Installed with django.contrib.auth. Unused (no PermissionsMixin); never holds data."
    ),
}

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _checked(table: str) -> str:
    if not _IDENTIFIER.fullmatch(table):
        raise ValueError(f"not a plain lower-case table name: {table!r}")
    return table


def enable_rls_sql(table: str) -> str:
    """SQL that puts ``table`` under chama RLS. The only place a policy is written."""
    table = _checked(table)
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {POLICY_NAME} ON {table} AS PERMISSIVE FOR ALL TO PUBLIC\n"
        f"    USING ({POLICY_PREDICATE})\n"
        f"    WITH CHECK ({POLICY_PREDICATE});"
    )


def disable_rls_sql(table: str) -> str:
    """Reverse of :func:`enable_rls_sql`, for migration rollback only."""
    table = _checked(table)
    return (
        f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table};\n"
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"
    )


def chama_rls(table: str) -> migrations.RunSQL:
    """Migration operation: ``operations = [..., chama_rls("ledger_entry")]``."""
    return migrations.RunSQL(enable_rls_sql(table), disable_rls_sql(table))


def user_self_read_sql(table: str) -> str:
    """SQL adding the read-only ``user_self_read`` policy. Only for tables in USER_READ_TABLES."""
    table = _checked(table)
    if table not in USER_READ_TABLES:
        raise ValueError(f"{table} is not in USER_READ_TABLES")
    return (
        f"CREATE POLICY {USER_POLICY_NAME} ON {table} AS PERMISSIVE FOR SELECT"
        f" TO {USER_POLICY_ROLE}\n    USING ({USER_POLICY_PREDICATE});"
    )


def user_self_read(table: str) -> migrations.RunSQL:
    """Migration operation, after ``chama_rls(table)``."""
    return migrations.RunSQL(
        user_self_read_sql(table), f"DROP POLICY IF EXISTS {USER_POLICY_NAME} ON {table};"
    )


def append_only_audit_sql(table: str) -> str:
    """SQL for an append-only audit table. Only for tables in AUDIT_TABLES."""
    table = _checked(table)
    if table not in AUDIT_TABLES:
        raise ValueError(f"{table} is not in AUDIT_TABLES")
    role = AUDIT_POLICY_ROLE
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"  # noqa: S608  table checked by _checked()
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {AUDIT_INSERT_POLICY} ON {table} AS PERMISSIVE FOR INSERT TO {role}\n"
        f"    WITH CHECK ({AUDIT_INSERT_CHECK});\n"
        f"CREATE POLICY {AUDIT_CHAMA_READ_POLICY} ON {table} AS PERMISSIVE FOR SELECT TO {role}\n"
        f"    USING ({POLICY_PREDICATE});\n"
        f"CREATE POLICY {AUDIT_USER_READ_POLICY} ON {table} AS PERMISSIVE FOR SELECT TO {role}\n"
        f"    USING ({AUDIT_USER_READ_PREDICATE});\n"
        f"REVOKE UPDATE, DELETE ON {table} FROM {role};"
    )


def append_only_audit(table: str) -> migrations.RunSQL:
    """Migration operation for an AUDIT_TABLES table (instead of ``chama_rls``)."""
    table = _checked(table)
    return migrations.RunSQL(
        append_only_audit_sql(table),
        f"GRANT UPDATE, DELETE ON {table} TO {AUDIT_POLICY_ROLE};\n"
        f"DROP POLICY IF EXISTS {AUDIT_USER_READ_POLICY} ON {table};\n"
        f"DROP POLICY IF EXISTS {AUDIT_CHAMA_READ_POLICY} ON {table};\n"
        f"DROP POLICY IF EXISTS {AUDIT_INSERT_POLICY} ON {table};\n"
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;",
    )


def user_isolation_sql(table: str) -> str:
    """SQL putting a per-user table under user RLS. Only for tables in USER_RLS_TABLES."""
    table = _checked(table)
    if table not in USER_RLS_TABLES:
        raise ValueError(f"{table} is not in USER_RLS_TABLES")
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {USER_ISOLATION_POLICY} ON {table} AS PERMISSIVE FOR ALL"
        f" TO {USER_POLICY_ROLE}\n"
        f"    USING ({USER_ISOLATION_PREDICATE})\n"
        f"    WITH CHECK ({USER_ISOLATION_PREDICATE});"
    )


def user_rls(table: str) -> migrations.RunSQL:
    """Migration operation for a USER_RLS_TABLES table (instead of ``chama_rls``)."""
    table = _checked(table)
    return migrations.RunSQL(
        user_isolation_sql(table),
        f"DROP POLICY IF EXISTS {USER_ISOLATION_POLICY} ON {table};\n"
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;",
    )


def in_row_chama_sql(chama_expr: str, body: str) -> str:
    """PL/pgSQL for a deferred constraint trigger: run ``body`` in the chama of the row being
    checked, then restore the previous setting.

    Deferred checks fire at COMMIT, after ``chama_context`` has already restored the setting
    (and in Django's test teardown with no context at all), so under FORCE RLS they would see no
    rows. The trigger switches to the chama the checked row belongs to: a row this transaction
    wrote under RLS ``WITH CHECK``, so the switch never reaches another chama's data. The body
    must only read and raise; it returns nothing to any caller. ``chama_expr`` is a trusted SQL
    expression such as ``NEW.chama_id``, never input. The function needs a ``previous_chama
    text`` variable."""
    setting = CHAMA_SETTING
    return (
        f"    previous_chama := current_setting('{setting}', true);\n"
        f"    PERFORM set_config('{setting}', ({chama_expr})::text, true);\n"
        f"{body}\n"
        f"    PERFORM set_config('{setting}', COALESCE(previous_chama, ''), true);"
    )


def for_each_chama_sql(body: str) -> str:
    """A ``DO`` block that runs ``body`` once per chama, inside that chama's RLS context.

    For data migrations on chama tables. Every chama table is under FORCE RLS
    and ``app_owner`` has no BYPASSRLS, so a migration sees a chama's rows only
    with ``app.current_chama`` set. The loop reads chama IDs from ``organizations_chama``
    (RLS-exempt) and sets the context with ``is_local`` (transaction-scoped), then clears it.
    No policy is changed. ``body`` is PL/pgSQL; it may use the variables ``acct uuid`` and
    ``n bigint``.
    """
    setting = CHAMA_SETTING
    loop = "    FOR acct IN SELECT id FROM organizations_chama ORDER BY id LOOP\n"
    return (
        "DO $$\nDECLARE\n    acct uuid;\n    n bigint;\nBEGIN\n"
        f"{loop}"
        f"        PERFORM set_config('{setting}', acct::text, true);\n"
        f"{body}\n"
        "    END LOOP;\n"
        f"    PERFORM set_config('{setting}', '', true);\n"
        "END\n$$;"
    )


# ---------------------------------------------------------------- chama context


class ChamaContextError(RuntimeError):
    """Missing, malformed or conflicting chama context."""


def _parse_uuid(value: object, label: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            pass
    raise ChamaContextError(f"{label} must be a UUID")


def _get_setting(name: str) -> uuid.UUID | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        row = cursor.fetchone()
    value = row[0] if row else None
    return uuid.UUID(value) if value else None


def _set_setting(name: str, value: str) -> None:
    with connection.cursor() as cursor:
        # is_local=true: the value ends with the transaction (or savepoint rollback), so a pooled
        # connection never carries one chama's or user's context into the next request or task.
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])


@contextmanager
def _setting_context(name: str, value: uuid.UUID, what: str) -> Iterator[uuid.UUID]:
    with transaction.atomic():
        previous = _get_setting(name)
        if previous is not None and previous != value:
            raise ChamaContextError(f"already inside a different {what}'s context")
        _set_setting(name, str(value))
        yield value
        # Normal exit: restore the outer value (matters only when nested in a larger
        # transaction). On an exception the savepoint/transaction rollback reverts it.
        _set_setting(name, str(previous) if previous else "")


def current_chama_id() -> uuid.UUID | None:
    """The chama whose rows this transaction can see, or None (sees no chama rows)."""
    return _get_setting(CHAMA_SETTING)


def current_user_id() -> uuid.UUID | None:
    """The user whose own rows (USER_READ_TABLES) this transaction can read, or None."""
    return _get_setting(USER_SETTING)


@contextmanager
def chama_context(chama_id: uuid.UUID | str) -> Iterator[uuid.UUID]:
    """Run the block in a transaction that can see only ``chama_id``'s rows.

    Keep the block short: it holds a database transaction, so never make network calls
    (WhatsApp, Daraja, SMS) inside it. Nesting with the same chama is allowed;
    nesting with a different chama raises, so one code path can never mix two chamas.
    """
    with _setting_context(CHAMA_SETTING, _parse_uuid(chama_id, "chama_id"), "chama") as a:
        yield a


@contextmanager
def user_context(user_id: uuid.UUID | str) -> Iterator[uuid.UUID]:
    """Run the block in a transaction that can READ the user's own rows in USER_READ_TABLES.

    TRUSTED CALLERS ONLY: the authentication middleware after verifying the session
    token, and the OTP-verify service after a code is verified. Never pass a value taken from
    request input. ``tests/test_user_context.py`` pins the call sites. Has no effect while an
    chama context is set (the policy requires no chama context). Writes are never allowed
    by it. Nesting with a different user raises.
    """
    with _setting_context(USER_SETTING, _parse_uuid(user_id, "user_id"), "user") as u:
        yield u


def active_chama_ids() -> list[uuid.UUID]:
    """IDs of active chamas, for jobs that fan out across chamas (one ChamaTask each).

    Calls the SECURITY DEFINER database function of the same name; it needs no chama context
    and returns IDs only.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM public.active_chama_ids() AS id")
        return [row[0] for row in cursor.fetchall()]


class ChamaTask(Task):  # type: ignore[misc]  # celery ships no type information
    """Celery base class for work on one chama's data.

    Call with ``chama_id=`` as a keyword argument; the task body runs inside
    :func:`chama_context`. Without it the task refuses to run. A task that does not use this
    base has no chama context and sees no chama rows (fail closed).

    The body is one transaction: tasks that call external providers must not use this base for
    the network call; use short ``chama_context`` blocks around the database work instead.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if "chama_id" not in kwargs:
            raise ChamaContextError(f"{self.name} must be called with chama_id=")
        with chama_context(kwargs["chama_id"]):
            return super().__call__(*args, **kwargs)
