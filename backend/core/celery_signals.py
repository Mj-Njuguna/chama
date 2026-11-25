"""Propagate the correlation ID from the publisher to the Celery worker."""

from typing import Any

from celery.signals import before_task_publish, task_postrun, task_prerun

from core.correlation import (
    CONTEXT_KEY,
    accept_or_new,
    bind_correlation_id,
    get_correlation_id,
)
from core.logging import clear_context


def add_correlation_header(headers: dict[str, Any] | None = None, **_: Any) -> None:
    if headers is not None and CONTEXT_KEY not in headers:
        correlation_id = get_correlation_id()
        if correlation_id:
            headers[CONTEXT_KEY] = correlation_id


def bind_task_correlation(task: Any = None, **_: Any) -> None:
    clear_context()
    request = getattr(task, "request", None)
    incoming = _incoming_correlation_id(request)
    bind_correlation_id(accept_or_new(incoming if isinstance(incoming, str) else None))


def clear_task_context(**_: Any) -> None:
    clear_context()


def _incoming_correlation_id(request: Any) -> str | None:
    """Custom headers surface either as request attributes or under ``request.headers``."""
    if request is None:
        return None
    value = getattr(request, CONTEXT_KEY, None)
    if value is None:
        headers = getattr(request, "headers", None) or {}
        value = headers.get(CONTEXT_KEY) if isinstance(headers, dict) else None
    return value if isinstance(value, str) else None


# Connected explicitly rather than with decorators so mypy keeps the functions typed.
before_task_publish.connect(add_correlation_header, weak=False)
task_prerun.connect(bind_task_correlation, weak=False)
task_postrun.connect(clear_task_context, weak=False)
