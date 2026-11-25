-- Cluster-level database roles. Idempotent: safe to run on every `just up` / `just test`.
--
-- Run as a superuser with psql variables (see apply-roles.sh):
--   dbname          the application database
--   owner_password  password for app_owner
--   rw_password     password for app_rw
--
-- app_owner  owns the schema and every table; runs migrations only. Not superuser, no BYPASSRLS.
--            CREATEDB only so the test runner can create the test database.
-- app_rw     used by the app, Celery worker and beat, and every test. Reads and writes rows only;
--            owns nothing, is a member of nothing, and is always subject to row-level security.
--
-- Per-database privileges (schema usage, default grants, TEMP revocation) live in the
-- identity 0001 migration so they also apply to the test database and to production.
-- In production these roles are created by infrastructure from this same file.

\set ON_ERROR_STOP on

SELECT 'CREATE ROLE app_owner'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_owner')
\gexec

SELECT 'CREATE ROLE app_rw'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_rw')
\gexec

-- Re-asserted on every run, so drift (e.g. a manual ALTER ROLE) is corrected.
SELECT format(
    'ALTER ROLE app_owner WITH LOGIN NOSUPERUSER NOBYPASSRLS CREATEDB NOCREATEROLE NOREPLICATION '
    'PASSWORD %L',
    :'owner_password'
)
\gexec

SELECT format(
    'ALTER ROLE app_rw WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION '
    'PASSWORD %L',
    :'rw_password'
)
\gexec

-- app_rw must not be able to SET ROLE to anything (in particular not to app_owner).
DO $$
DECLARE
    grant_row record;
BEGIN
    FOR grant_row IN
        SELECT r.rolname AS granted
        FROM pg_auth_members m
        JOIN pg_roles r ON r.oid = m.roleid
        JOIN pg_roles u ON u.oid = m.member
        WHERE u.rolname = 'app_rw'
    LOOP
        EXECUTE format('REVOKE %I FROM app_rw', grant_row.granted);
    END LOOP;
END
$$;

-- app_owner owns the application database, so it also owns its public schema
-- (PostgreSQL 15+: public is owned by pg_database_owner).
SELECT format('ALTER DATABASE %I OWNER TO app_owner', :'dbname')
\gexec

-- Databases created before have tables owned by the bootstrap superuser. Hand them to
-- app_owner so later migrations (run as app_owner) can alter them. Sequences backing identity
-- columns follow their table automatically.
DO $$
DECLARE
    rel record;
BEGIN
    FOR rel IN
        SELECT c.relname, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm')
          AND pg_get_userbyid(c.relowner) <> 'app_owner'
    LOOP
        EXECUTE format(
            'ALTER %s public.%I OWNER TO app_owner',
            CASE rel.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
            rel.relname
        );
    END LOOP;
END
$$;
