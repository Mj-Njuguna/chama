"""Request middleware: correlation ID and access log; chama context for authenticated requests."""

import time
from collections.abc import Callable

from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve

from core.correlation import HEADER, accept_or_new, bind_correlation_id
from core.logging import clear_context, get_logger
from core.tenancy import AUTHENTICATED_CHAMA_ATTR, chama_context

log = get_logger("request")

_QUIET_PATHS = frozenset({"/healthz", "/readyz"})


class CorrelationIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        clear_context()
        correlation_id = accept_or_new(request.headers.get(HEADER))
        bind_correlation_id(correlation_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response[HEADER] = correlation_id
            if request.path not in _QUIET_PATHS:
                # Path only, never the query string (it may carry phone numbers or codes).
                log.info(
                    "http_request",
                    method=request.method,
                    path=request.path,
                    status_code=response.status_code,
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
            return response
        finally:
            clear_context()


class ChamaContextMiddleware:
    """Runs each authenticated request inside the chama context of its session.

    The chama comes only from ``request.authenticated_chama_id``, which only the
    authentication middleware sets (from the verified session token). Request headers, body and
    query string are never consulted. ``core.checks`` refuses to start if the authentication
    middleware is missing or runs after this one.

    Requests without an authenticated chama (health checks, webhooks) get no context and see
    no chama rows. Neither do views that declare ``chama_context = False``: the few that
    work across the user's chamas (``/me``, ``/chamas``, switch-chama), which read the
    user's own memberships and therefore must run outside any chama context. Like
    ATOMIC_REQUESTS, a 5xx response rolls the request's writes back.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        chama_id = getattr(request, AUTHENTICATED_CHAMA_ATTR, None)
        if chama_id is None or not _wants_chama_context(request):
            return self.get_response(request)
        with chama_context(chama_id):
            response = self.get_response(request)
            if response.status_code >= 500:
                transaction.set_rollback(True)
            return response


def _wants_chama_context(request: HttpRequest) -> bool:
    """False only for a view that declares ``chama_context = False`` (default: True)."""
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return True
    view = getattr(match.func, "view_class", match.func)
    return getattr(view, "chama_context", True) is not False
