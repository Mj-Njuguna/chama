"""Tenant isolation: cross-chama reads and writes fail, in requests and in background jobs.

Proven on the test-only probe table, which carries exactly the generated policy that every real
chama table must carry (tests/test_rls_catalog.py).
"""

import json

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import Client

from core.tenancy import ChamaContextError, chama_context, current_chama_id
from tests.rls_probe import tasks
from tests.rls_probe.models import ProbeRecord

pytestmark = pytest.mark.django_db


def _b_row_still_intact(b, b_row) -> None:
    with chama_context(b.id):
        assert ProbeRecord.objects.get(id=b_row.id).name == "b-secret"


# ---------------------------------------------------------------- ISO-01
# Chama A user queries Chama B's rows: zero rows, even with a guessed ID.


def test_iso_01_orm_reads_by_guessed_id_return_nothing(two_chamas):
    a, b, b_row = two_chamas
    with chama_context(a.id):
        assert not ProbeRecord.objects.filter(id=b_row.id).exists()
        with pytest.raises(ProbeRecord.DoesNotExist):
            ProbeRecord.objects.get(id=b_row.id)
        assert not ProbeRecord.objects.filter(chama_id=b.id).exists()
        assert sorted(ProbeRecord.objects.values_list("name", flat=True)) == ["a-1", "a-2"]


def test_iso_01_raw_sql_cannot_see_other_chama(two_chamas):
    a, b, b_row = two_chamas
    with chama_context(a.id), connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM rls_probe_proberecord WHERE id = %s OR chama_id = %s",
            [b_row.id, b.id],
        )
        assert cursor.fetchone() == (0,)


def test_iso_01_updates_and_deletes_by_guessed_id_affect_nothing(two_chamas):
    a, b, b_row = two_chamas
    with chama_context(a.id):
        assert ProbeRecord.objects.filter(id=b_row.id).update(name="pwned") == 0
        assert ProbeRecord.objects.filter(id=b_row.id).delete()[0] == 0
    _b_row_still_intact(b, b_row)


def test_iso_01_cannot_insert_or_move_rows_into_other_chama(two_chamas):
    a, b, _ = two_chamas
    with chama_context(a.id):
        with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
            ProbeRecord.objects.create(chama_id=b.id, name="planted")
        own = ProbeRecord.objects.get(name="a-1")
        with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
            ProbeRecord.objects.filter(id=own.id).update(chama_id=b.id)
    with chama_context(b.id):
        assert list(ProbeRecord.objects.values_list("name", flat=True)) == ["b-secret"]


def test_iso_01_over_http(two_chamas, fake_auth, client):
    a, _, _ = two_chamas
    fake_auth["token-a"] = a.id
    body = client.get("/probe/records", HTTP_AUTHORIZATION="Test token-a").json()
    assert body["names"] == ["a-1", "a-2"]


# ---------------------------------------------------------------- ISO-02
# Request body or query string contains another chama_id: ignored; the authenticated
# membership (session) wins.


def test_iso_02_query_string_and_header_chama_id_are_ignored(two_chamas, fake_auth, client):
    a, b, _ = two_chamas
    fake_auth["token-a"] = a.id
    body = client.get(
        f"/probe/records?chama_id={b.id}",
        HTTP_AUTHORIZATION="Test token-a",
        HTTP_X_CHAMA_ID=str(b.id),
    ).json()
    assert body == {"current_chama": str(a.id), "names": ["a-1", "a-2"]}


def test_iso_02_body_chama_id_is_ignored_on_write(two_chamas, fake_auth, client):
    a, b, _ = two_chamas
    fake_auth["token-a"] = a.id
    response = client.post(
        f"/probe/records?chama_id={b.id}",
        data=json.dumps({"name": "new", "chama_id": str(b.id)}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Test token-a",
        HTTP_X_CHAMA_ID=str(b.id),
    )
    assert response.json() == {"current_chama": str(a.id), "names": ["a-1", "a-2", "new"]}
    with chama_context(b.id):
        assert list(ProbeRecord.objects.values_list("name", flat=True)) == ["b-secret"]


def test_iso_02_view_that_trusts_the_body_is_still_stopped_by_rls(two_chamas, fake_auth):
    """Defence in depth: even buggy view code that uses a body chama_id cannot write to B."""
    a, b, _ = two_chamas
    fake_auth["token-a"] = a.id
    response = Client(raise_request_exception=False).post(
        "/probe/naive",
        data=json.dumps({"name": "planted", "chama_id": str(b.id)}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Test token-a",
    )
    assert response.status_code == 500
    with chama_context(b.id):
        assert list(ProbeRecord.objects.values_list("name", flat=True)) == ["b-secret"]


def test_iso_02_unauthenticated_request_with_chama_id_sees_nothing(two_chamas, fake_auth, client):
    _, b, _ = two_chamas
    body = client.get(f"/probe/records?chama_id={b.id}", HTTP_X_CHAMA_ID=str(b.id)).json()
    assert body == {"current_chama": None, "names": []}


# ---------------------------------------------------------------- ISO-03
# Celery job runs without app.current_chama set: fails closed (no rows, or error), never
# returns all chamas.


def test_iso_03_plain_task_reads_no_rows(two_chamas):
    assert current_chama_id() is None
    assert tasks.count_without_context.delay().get() == 0


def test_iso_03_plain_task_writes_are_refused(two_chamas):
    a, _, _ = two_chamas
    with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
        tasks.create_without_context.delay(chama_id=str(a.id)).get()


def test_iso_03_chama_task_without_chama_id_refuses_to_run(two_chamas):
    with pytest.raises(ChamaContextError):
        tasks.names_for_chama()


def test_iso_03_empty_setting_also_fails_closed(two_chamas):
    """A context reset to '' (as chama_context leaves it) must behave like unset."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_chama', '', true)")
        assert tasks.count_without_context.delay().get() == 0
