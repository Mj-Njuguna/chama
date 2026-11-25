import pytest
from django.test import Client

from core import health


def test_healthz_is_ok_without_touching_dependencies(client: Client, monkeypatch):
    def boom() -> None:
        raise AssertionError("liveness must not check dependencies")

    monkeypatch.setattr(health, "CHECKS", {"database": boom, "redis": boom})
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_ok_when_postgres_and_redis_reachable(client: Client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_readyz_503_without_leaking_error_details(client: Client, monkeypatch):
    def down() -> None:
        raise ConnectionError("redis://secret-host:6379 refused")

    monkeypatch.setattr(health, "CHECKS", {"database": lambda: None, "redis": down})
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "ok", "redis": "unavailable"},
    }
    assert b"secret-host" not in response.content


def test_health_endpoints_reject_post(client: Client):
    assert client.post("/healthz").status_code == 405
