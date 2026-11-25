"""chama_context(), ChamaContextMiddleware and ChamaTask."""

import uuid

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from core.tenancy import ChamaContextError, chama_context, current_chama_id
from tests.rls_probe import tasks
from tests.rls_probe.models import ProbeRecord

pytestmark = pytest.mark.django_db


def _names() -> list[str]:
    return sorted(ProbeRecord.objects.values_list("name", flat=True))


# ---------------------------------------------------------------- chama_context()


def test_no_context_by_default_and_sees_nothing(two_chamas):
    assert current_chama_id() is None
    assert _names() == []


def test_context_shows_only_that_chama_and_is_removed_after(two_chamas):
    a, _, _ = two_chamas
    with chama_context(a.id):
        assert current_chama_id() == a.id
        assert _names() == ["a-1", "a-2"]
    assert current_chama_id() is None
    assert _names() == []


def test_context_accepts_string_ids(two_chamas):
    a, _, _ = two_chamas
    with chama_context(str(a.id)) as chama:
        assert chama == a.id


@pytest.mark.parametrize("bad", [None, "", "not-a-uuid", 42, "x'; SET ROLE app_owner; --"])
def test_context_rejects_malformed_ids(bad):
    with pytest.raises(ChamaContextError), chama_context(bad):
        pass


def test_nesting_same_chama_is_allowed(two_chamas):
    a, _, _ = two_chamas
    with chama_context(a.id), chama_context(a.id):
        assert _names() == ["a-1", "a-2"]
    assert current_chama_id() is None


def test_nesting_a_different_chama_raises(two_chamas):
    a, b, _ = two_chamas
    with chama_context(a.id):
        with pytest.raises(ChamaContextError), chama_context(b.id):
            pass
        assert current_chama_id() == a.id


def test_context_is_reverted_when_the_block_raises(two_chamas):
    a, _, _ = two_chamas
    with pytest.raises(ZeroDivisionError), chama_context(a.id):
        ProbeRecord.objects.create(chama=a, name="rolled-back")
        1 / 0  # noqa: B018
    assert current_chama_id() is None
    with chama_context(a.id):
        assert "rolled-back" not in _names()


def test_writes_without_context_are_refused(two_chamas):
    a, _, _ = two_chamas
    with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
        ProbeRecord.objects.create(chama=a, name="no-context")


# ---------------------------------------------------------------- middleware


def test_authenticated_request_runs_in_its_chama_context(two_chamas, fake_auth, client):
    a, _, _ = two_chamas
    fake_auth["token-a"] = a.id
    body = client.get("/probe/records", HTTP_AUTHORIZATION="Test token-a").json()
    assert body == {"current_chama": str(a.id), "names": ["a-1", "a-2"]}


def test_unauthenticated_request_gets_no_context(two_chamas, fake_auth, client):
    body = client.get("/probe/records").json()
    assert body == {"current_chama": None, "names": []}
    body = client.get("/probe/records", HTTP_AUTHORIZATION="Test unknown-token").json()
    assert body == {"current_chama": None, "names": []}


def test_context_does_not_leak_between_requests(two_chamas, fake_auth, client):
    a, b, _ = two_chamas
    fake_auth.update({"token-a": a.id, "token-b": b.id})
    auth_a = {"HTTP_AUTHORIZATION": "Test token-a"}
    auth_b = {"HTTP_AUTHORIZATION": "Test token-b"}
    assert client.get("/probe/records", **auth_a).json()["names"] == ["a-1", "a-2"]
    assert client.get("/probe/records").json()["names"] == []
    assert client.get("/probe/records", **auth_b).json()["names"] == ["b-secret"]
    assert current_chama_id() is None


def test_server_error_rolls_back_the_request_writes(two_chamas, fake_auth):
    a, _, _ = two_chamas
    fake_auth["token-a"] = a.id
    response = Client(raise_request_exception=False).post(
        "/probe/fail", HTTP_AUTHORIZATION="Test token-a"
    )
    assert response.status_code == 500
    with chama_context(a.id):
        assert _names() == ["a-1", "a-2"]


def test_placeholder_auth_middleware_authenticates_nobody(two_chamas, client, settings):
    settings.ROOT_URLCONF = "tests.rls_probe.urls"
    body = client.get("/probe/records", HTTP_AUTHORIZATION="Test anything").json()
    assert body == {"current_chama": None, "names": []}


# ---------------------------------------------------------------- ChamaTask


def test_chama_task_runs_in_the_given_chama(two_chamas):
    _, b, _ = two_chamas
    result = tasks.names_for_chama.delay(chama_id=str(b.id)).get()
    assert result == {"context": str(b.id), "names": ["b-secret"]}
    assert current_chama_id() is None


def test_chama_task_refuses_to_run_without_chama_id(two_chamas):
    # Celery's signature check refuses first when the task declares chama_id...
    with pytest.raises(TypeError, match="chama_id"):
        tasks.names_for_chama.delay()
    # ...and ChamaTask itself refuses on any path that skips that check.
    with pytest.raises(ChamaContextError, match="chama_id"):
        tasks.names_for_chama()


def test_chama_task_rejects_malformed_chama_id():
    with pytest.raises(ChamaContextError):
        tasks.names_for_chama.delay(chama_id="nope").get()


def test_chama_task_for_unknown_chama_sees_nothing(two_chamas):
    result = tasks.names_for_chama.delay(chama_id=str(uuid.uuid4())).get()
    assert result["names"] == []
