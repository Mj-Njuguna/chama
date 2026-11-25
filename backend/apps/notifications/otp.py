"""Sending one-time codes: WhatsApp first, SMS once if WhatsApp does not deliver. Internal to
notifications; other modules call ``apps.notifications.services.send_otp``.

Real providers are added once their APIs are verified. Until then only
:mod:`apps.notifications.fake` exists, and production configures no sender at all, so OTP
requests there fail closed. No provider API details are assumed here: a sender only reports one
of three outcomes.

Every provider call has a hard wall-clock timeout (``MESSAGING_PROVIDER_TIMEOUT_SECONDS``): the
call runs in a worker thread and the request stops waiting when the time is up. Callers get the
same :class:`OtpSendFailed` for every failure, whatever the number, so a response never reveals
anything about it.
"""

import enum
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from functools import cache
from typing import Protocol

import structlog
from django.conf import settings
from django.utils.module_loading import import_string

log = structlog.get_logger(__name__)

# Bounded: at most this many provider calls in flight per process; a call that timed out keeps
# its worker thread until the provider's own (shorter) network timeout ends it.
_MAX_PROVIDER_THREADS = 16


class OtpChannel(enum.StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"


# Order of attempts. SMS is used only when WhatsApp did not deliver.
CHANNEL_ORDER = (OtpChannel.WHATSAPP, OtpChannel.SMS)


class SendOutcome(enum.StrEnum):
    ACCEPTED = "accepted"  # the provider took the message for delivery
    UNDELIVERABLE = "undeliverable"  # this number cannot receive on this channel
    FAILED = "failed"  # provider error


class OtpSender(Protocol):
    """One channel's provider. Must not log the code or the full number."""

    def send_otp(self, phone_e164: str, code: str, *, timeout: float) -> SendOutcome: ...


@dataclass(frozen=True)
class SendFailure:
    channel: OtpChannel
    reason: str  # undeliverable, failed, timeout, error, not_configured


@dataclass(frozen=True)
class OtpSendResult:
    channel: OtpChannel  # the channel that accepted the message
    failures: tuple[SendFailure, ...] = field(default_factory=tuple)


class OtpSendFailed(Exception):
    """No channel accepted the code. Deliberately carries nothing about the number."""

    def __init__(self, failures: tuple[SendFailure, ...]) -> None:
        super().__init__("the code could not be sent")
        self.failures = failures


@cache
def _executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=_MAX_PROVIDER_THREADS, thread_name_prefix="otp-send")


def _sender(channel: OtpChannel) -> OtpSender | None:
    path = settings.OTP_SENDERS.get(channel.value)
    if not path:
        return None
    factory: Callable[[], OtpSender] = import_string(path)
    return factory()


def _attempt(channel: OtpChannel, phone_e164: str, code: str) -> SendOutcome | str:
    """The outcome, or a failure reason string for timeouts, errors and missing senders."""
    sender = _sender(channel)
    if sender is None:
        return "not_configured"
    timeout = float(settings.MESSAGING_PROVIDER_TIMEOUT_SECONDS)
    future = _executor().submit(sender.send_otp, phone_e164, code, timeout=timeout)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        return "timeout"
    except Exception:  # a provider bug must never reach the user as anything but a failure
        log.exception("otp_sender_error", channel=channel.value)
        return "error"


def send_otp(phone_e164: str, code: str) -> OtpSendResult:
    """Send ``code`` by WhatsApp, or by SMS once if WhatsApp did not take it.

    Never call inside a database transaction ( rule 2). Raises :class:`OtpSendFailed`
    when no channel accepted the message.
    """
    failures: list[SendFailure] = []
    for channel in CHANNEL_ORDER:
        outcome = _attempt(channel, phone_e164, code)
        if outcome == SendOutcome.ACCEPTED:
            return OtpSendResult(channel=channel, failures=tuple(failures))
        failures.append(SendFailure(channel, str(outcome)))
        log.warning("otp_send_attempt_failed", channel=channel.value, reason=str(outcome))
    raise OtpSendFailed(tuple(failures))
