"""Public interface of the ``notifications`` module.

WhatsApp, SMS and push delivery: one-time codes, invitations, reminders, the in-app group feed and
group webhooks.

Boundary rules:
- Other modules may import ONLY ``apps.notifications.services`` and ``apps.notifications.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""

from apps.notifications.invites import InviteSendResult, send_invite
from apps.notifications.otp import (
    OtpChannel,
    OtpSendFailed,
    OtpSendResult,
    SendFailure,
    send_otp,
)
from apps.notifications.push import send_push

__all__ = [
    "InviteSendResult",
    "OtpChannel",
    "OtpSendFailed",
    "OtpSendResult",
    "SendFailure",
    "send_invite",
    "send_otp",
    "send_push",
]
