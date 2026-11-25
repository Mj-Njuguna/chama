"""Operator signals (SIM swap, number verification): interface and no-op.

Internal to identity. Whether Safaricom or any operator exposes such APIs is an open item
; no operator API detail is assumed here and none may be integrated until its
availability is confirmed. A real provider implements :class:`OperatorSignalProvider`.

A result is a **risk signal that escalates verification, never proof**: ``elevated`` may make a
flow ask for more (for example KYC review before a number change), but no result ever grants
access, skips a step-up or replaces an OTP. ``unknown`` (the no-op, and any provider failure)
changes nothing.
"""

import enum
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string


class SignalLevel(enum.StrEnum):
    UNKNOWN = "unknown"  # no data: behave exactly as without a provider
    NONE = "none"  # the provider saw nothing unusual (still not proof of anything)
    ELEVATED = "elevated"  # escalate verification


class OperatorSignalProvider(Protocol):
    """Checks one E.164 number. Must not log it, and must answer UNKNOWN on any failure."""

    def sim_swap_risk(self, phone_e164: str) -> SignalLevel: ...


class NoOpOperatorSignalProvider:
    def sim_swap_risk(self, phone_e164: str) -> SignalLevel:
        return SignalLevel.UNKNOWN


def operator_signals() -> OperatorSignalProvider:
    """The configured provider (``OPERATOR_SIGNAL_PROVIDER``); the no-op by default."""
    provider: OperatorSignalProvider = import_string(settings.OPERATOR_SIGNAL_PROVIDER)()
    return provider


def escalates(level: SignalLevel) -> bool:
    """The only decision a signal may drive: whether to ask for MORE verification."""
    return level == SignalLevel.ELEVATED
