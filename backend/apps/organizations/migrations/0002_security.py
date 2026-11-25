"""Row-level security and privileges for the organizations tables.

- ``organizations_chama`` is the tenant root (RLS-exempt, see ``core.tenancy``).
- Every table with ``chama_id`` is under chama RLS; memberships are also readable by their own
  verified user when no chama is active (``user_self_read``).
- Memberships, constitutions and pending changes are never deleted by the app.
- ``active_chama_ids()``: a SECURITY DEFINER function returning IDs only, for jobs that fan out
  across chamas without any chama context.
"""

from django.db import migrations

from core.tenancy import chama_rls, user_self_read

ACTIVE_CHAMA_IDS = """
CREATE FUNCTION public.active_chama_ids()
    RETURNS SETOF uuid
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT c.id FROM public.organizations_chama AS c WHERE c.status = 'active' ORDER BY c.id
$$;
REVOKE ALL ON FUNCTION public.active_chama_ids() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.active_chama_ids() TO app_rw;
"""

NO_DELETE = """\
REVOKE DELETE ON organizations_chama FROM app_rw;
REVOKE DELETE ON organizations_membership FROM app_rw;
REVOKE DELETE ON organizations_constitution FROM app_rw;
REVOKE DELETE ON organizations_pendingchange FROM app_rw;"""

GRANT_DELETE = """\
GRANT DELETE ON organizations_pendingchange TO app_rw;
GRANT DELETE ON organizations_constitution TO app_rw;
GRANT DELETE ON organizations_membership TO app_rw;
GRANT DELETE ON organizations_chama TO app_rw;"""


class Migration(migrations.Migration):
    dependencies = [("organizations", "0001_initial")]

    operations = [
        migrations.RunSQL(ACTIVE_CHAMA_IDS, "DROP FUNCTION public.active_chama_ids();"),
        chama_rls("organizations_membership"),
        user_self_read("organizations_membership"),
        chama_rls("organizations_constitution"),
        chama_rls("organizations_pendingchange"),
        migrations.RunSQL(NO_DELETE, GRANT_DELETE),
    ]
