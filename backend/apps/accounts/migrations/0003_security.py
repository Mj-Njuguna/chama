"""Row-level security, privileges and database foreign keys for the accounts tables.

- ``accounts_securityevent``: append-only audit table (insert and select only).
- ``accounts_device``, ``accounts_steppin``, ``accounts_pushtoken``: per-user RLS.
- Sessions and retired tokens are never deleted by the app; retired tokens are write-once.
- Session, token and push-token IDs are plain columns in the models (no ORM relations), backed
  here by database foreign keys with explicit ``ON DELETE`` rules.
- ``django.contrib.auth`` permission tables stay empty: access is decided by membership roles.
"""

from django.db import migrations

from core.tenancy import append_only_audit, user_rls

FOREIGN_KEYS = """\
ALTER TABLE accounts_authsession ADD CONSTRAINT accounts_authsession_user_fk
    FOREIGN KEY (user_id) REFERENCES accounts_user (id) ON DELETE RESTRICT;
ALTER TABLE accounts_authsession ADD CONSTRAINT accounts_authsession_device_fk
    FOREIGN KEY (device_id, user_id) REFERENCES accounts_device (id, user_id) ON DELETE RESTRICT;
ALTER TABLE accounts_authtoken ADD CONSTRAINT accounts_authtoken_session_fk
    FOREIGN KEY (session_id) REFERENCES accounts_authsession (id) ON DELETE CASCADE;
ALTER TABLE accounts_pushtoken ADD CONSTRAINT accounts_pushtoken_device_fk
    FOREIGN KEY (device_id, user_id) REFERENCES accounts_device (id, user_id) ON DELETE RESTRICT;
ALTER TABLE accounts_pushtoken ADD CONSTRAINT accounts_pushtoken_session_fk
    FOREIGN KEY (session_id) REFERENCES accounts_authsession (id) ON DELETE CASCADE;"""

DROP_FOREIGN_KEYS = """\
ALTER TABLE accounts_pushtoken DROP CONSTRAINT accounts_pushtoken_session_fk;
ALTER TABLE accounts_pushtoken DROP CONSTRAINT accounts_pushtoken_device_fk;
ALTER TABLE accounts_authtoken DROP CONSTRAINT accounts_authtoken_session_fk;
ALTER TABLE accounts_authsession DROP CONSTRAINT accounts_authsession_device_fk;
ALTER TABLE accounts_authsession DROP CONSTRAINT accounts_authsession_user_fk;"""

PRIVILEGES = """\
REVOKE DELETE ON accounts_authsession FROM app_rw;
REVOKE DELETE ON accounts_authtoken FROM app_rw;
REVOKE UPDATE ON accounts_authtoken FROM app_rw;
REVOKE DELETE ON accounts_steppin FROM app_rw;"""

UNDO_PRIVILEGES = """\
GRANT DELETE ON accounts_steppin TO app_rw;
GRANT UPDATE ON accounts_authtoken TO app_rw;
GRANT DELETE ON accounts_authtoken TO app_rw;
GRANT DELETE ON accounts_authsession TO app_rw;"""

EMPTY_AUTH_TABLES = """
DELETE FROM auth_group_permissions;
DELETE FROM auth_group;
DELETE FROM auth_permission;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("accounts", "0002_initial"),
    ]

    operations = [
        append_only_audit("accounts_securityevent"),
        user_rls("accounts_device"),
        user_rls("accounts_steppin"),
        user_rls("accounts_pushtoken"),
        migrations.RunSQL(FOREIGN_KEYS, DROP_FOREIGN_KEYS),
        migrations.RunSQL(PRIVILEGES, UNDO_PRIVILEGES),
        migrations.RunSQL(EMPTY_AUTH_TABLES, reverse_sql=migrations.RunSQL.noop),
    ]
