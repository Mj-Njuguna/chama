"""Correlation IDs: one ID follows a request through logs, Celery tasks and outbound calls."""

import re
import uuid

from core.logging import bind_context, current_context

HEADER = "X-Request-ID"
CONTEXT_KEY = "correlation_id"
# Accept a caller-supplied ID only if it is short and boring; otherwise mint our own, so the
# header can't be used for log injection.
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def accept_or_new(candidate: str | None) -> str:
    if candidate and _VALID.match(candidate):
        return candidate
    return new_correlation_id()


def get_correlation_id() -> str | None:
    value = current_context().get(CONTEXT_KEY)
    return value if isinstance(value, str) else None


def bind_correlation_id(value: str) -> None:
    bind_context(**{CONTEXT_KEY: value})
