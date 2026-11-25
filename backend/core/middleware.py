"""Request middleware: correlation ID and access log."""

import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.correlation import HEADER, accept_or_new, bind_correlation_id
from core.logging import clear_context, get_logger

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
