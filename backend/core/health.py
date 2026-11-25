"""Health endpoints.

- ``/healthz`` (liveness): the process serves requests. No dependency checks, so a database
  outage doesn't make the orchestrator restart healthy app containers.
- ``/readyz`` (readiness): Postgres and Redis are reachable. Returns 503 if either is down.
Neither endpoint returns hostnames, versions or error details.
"""

from collections.abc import Callable

import redis
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from core.logging import get_logger

log = get_logger(__name__)


def _check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _check_redis() -> None:
    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.ping()
    finally:
        client.close()


CHECKS: dict[str, Callable[[], None]] = {
    "database": _check_database,
    "redis": _check_redis,
}


@require_GET
def healthz(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def readyz(_request: HttpRequest) -> JsonResponse:
    results: dict[str, str] = {}
    for name, check in CHECKS.items():
        try:
            check()
            results[name] = "ok"
        except Exception:
            log.warning("readiness_check_failed", check=name, exc_info=True)
            results[name] = "unavailable"
    healthy = all(value == "ok" for value in results.values())
    return JsonResponse(
        {"status": "ok" if healthy else "unavailable", "checks": results},
        status=200 if healthy else 503,
    )
