"""Push notifications: the provider interface and its fake. Nothing is sent yet.

Internal to notifications. A real provider is written only after the Expo push and FCM details
(token format, lifetime, error answers, whether a Firebase project is needed) are verified from
their documentation; no provider API detail is assumed here. A provider takes
one token and one message and reports only accepted or failed.

Payloads never carry amounts, names or phone numbers: a message is a ``kind``
(a short word) and a few IDs, and the app fetches the details after unlock. :class:`PushMessage`
refuses anything else.

Targets come from ``apps.accounts.services.push_targets_for_user``, which returns only tokens
whose session and device are live.
"""

import enum
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from django.conf import settings
from django.utils.module_loading import import_string

log = structlog.get_logger(__name__)

_KIND = re.compile(r"^[a-z_]{1,40}$")
_MAX_DATA_KEYS = 4


class PushOutcome(enum.StrEnum):
    ACCEPTED = "accepted"
    FAILED = "failed"


@dataclass(frozen=True)
class PushMessage:
    """``kind`` (e.g. ``payment_confirmed``) and at most four UUIDs. Nothing else fits."""

    kind: str
    data: dict[str, uuid.UUID] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _KIND.fullmatch(self.kind):
            raise ValueError("push kind must be a short lower-case word")
        if len(self.data) > _MAX_DATA_KEYS:
            raise ValueError("too many push data keys")
        for key, value in self.data.items():
            if not _KIND.fullmatch(key) or type(value) is not uuid.UUID:
                raise ValueError("push data holds only IDs under short lower-case keys")


class PushProvider(Protocol):
    """Sends one message to one token. Must not log the token."""

    def send(self, token_type: str, token: str, message: PushMessage) -> PushOutcome: ...


def send_push(
    targets: list[tuple[str, str]], kind: str, data: dict[str, uuid.UUID] | None = None
) -> int:
    """Send ``PushMessage(kind)`` to each ``(token_type, token)``; returns how many were accepted.
    No provider configured (production until one is verified): nothing is sent. A failure for
    one token never stops the others or raises to the caller."""
    message = PushMessage(kind, data or {})
    provider = push_provider()
    if provider is None:
        return 0
    accepted = 0
    for token_type, token in targets:
        try:
            outcome = provider.send(token_type, token, message)
        except Exception:  # deliberately broad: a push is best effort
            log.warning("push_send_failed", kind=kind)
            continue
        accepted += outcome == PushOutcome.ACCEPTED
    return accepted


def push_provider() -> PushProvider | None:
    """The configured provider, or None (production configures none until one is verified)."""
    path = settings.PUSH_PROVIDER
    if not path:
        return None
    factory: Callable[[], PushProvider] = import_string(path)
    return factory()
