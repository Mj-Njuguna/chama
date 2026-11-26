"""In-process domain events, delivered after the current transaction commits.

- A module defines its events as frozen dataclasses in its ``events.py``.
- Subscribers register a handler per event type, usually in their ``AppConfig.ready``:
  ``subscribe(PaymentConfirmed, handler)``. Handlers should be small and enqueue Celery work
  for anything slow or networked.
- :func:`publish` defers delivery with ``transaction.on_commit``: a rolled-back transaction
  publishes nothing, and handlers never see uncommitted rows. Outside a transaction it runs at
  once (Django's ``on_commit`` semantics).
- A failing handler is logged and does not stop the others or the publisher; the published
  fact (already committed) stays true.
"""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from django.db import transaction

from core.logging import get_logger

log = get_logger(__name__)

Handler = Callable[[Any], None]

_HANDLERS: dict[type, list[Handler]] = defaultdict(list)


def subscribe[E](event_type: type[E], handler: Callable[[E], None]) -> None:
    """Register ``handler`` for ``event_type`` (idempotent per handler)."""
    if handler not in _HANDLERS[event_type]:
        _HANDLERS[event_type].append(handler)


def unsubscribe(event_type: type, handler: Handler) -> None:
    if handler in _HANDLERS.get(event_type, []):
        _HANDLERS[event_type].remove(handler)


def handlers_for(event_type: type) -> list[Handler]:
    return list(_HANDLERS.get(event_type, []))


def _deliver(event: object) -> None:
    for handler in handlers_for(type(event)):
        try:
            handler(event)
        except Exception:
            log.exception("event_handler_failed", event_type=type(event).__name__)


def publish(event: object) -> None:
    """Deliver ``event`` to its subscribers after the current transaction commits."""
    transaction.on_commit(lambda: _deliver(event))
