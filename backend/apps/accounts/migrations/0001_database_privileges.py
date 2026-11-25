"""Per-database privileges for the two application roles.

The roles themselves are cluster-level and created by db/roles.sql (infrastructure in
production). Everything here is per-database, so it lives in a migration: it then applies to the
dev database, the test database and production alike.

This is the first migration of the project's own apps; it refuses to run as anyone but app_owner,
so a migration accidentally run as app_rw or as a superuser fails loudly instead of creating
objects with the wrong owner.
"""

from django.db import migrations

FORWARD = """
DO $$
BEGIN
    IF current_user <> 'app_owner' THEN
        RAISE EXCEPTION 'migrations must run as app_owner, not %', current_user;
    END IF;
    -- No temp tables for app_rw (or anyone else via PUBLIC).
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

-- Only app_owner (owner of the schema) creates objects.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_rw;

-- Every table app_owner creates from now on: row access for app_rw, nothing more
-- (no TRUNCATE, REFERENCES, TRIGGER, ownership).
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO app_rw;
-- Functions are not executable by PUBLIC by default; each is granted explicitly.
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Tables that already exist (Django's own, created before this migration).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_rw;

-- app_rw may read migration history (Django checks it at startup) but never change it.
REVOKE INSERT, UPDATE, DELETE ON django_migrations FROM app_rw;
"""

REVERSE = """
REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM app_rw;
REVOKE USAGE ON ALL SEQUENCES IN SCHEMA public FROM app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner GRANT EXECUTE ON FUNCTIONS TO PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    REVOKE USAGE ON SEQUENCES FROM app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_rw;
REVOKE USAGE ON SCHEMA public FROM app_rw;
"""


class Migration(migrations.Migration):
    initial = True
    dependencies = [("contenttypes", "0002_remove_content_type_name")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
