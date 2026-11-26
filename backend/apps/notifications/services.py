"""Public interface of the ``notifications`` module.

WhatsApp, SMS and push delivery: one-time codes, invitations, reminders, the in-app group feed and
group webhooks.

Boundary rules:
- Other modules may import ONLY ``apps.notifications.services`` and ``apps.notifications.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""

import uuid

from apps.notifications.feed import notify_push, publish_feed_event
from apps.notifications.invites import InviteSendResult, send_invite
from apps.notifications.messaging import TEMPLATES, queue_message
from apps.notifications.otp import (
    OtpChannel,
    OtpSendFailed,
    OtpSendResult,
    SendFailure,
    send_otp,
)
from apps.notifications.push import send_push
from apps.notifications.text import format_kes

__all__ = [
    "TEMPLATES",
    "InviteSendResult",
    "OtpChannel",
    "OtpSendFailed",
    "OtpSendResult",
    "SendFailure",
    "format_kes",
    "notify_push",
    "publish_feed_event",
    "send_invite",
    "send_member_message",
    "send_otp",
    "send_push",
]


def send_member_message(
    chama_id: uuid.UUID,
    member_id: uuid.UUID,
    template: str,
    params: list[str],
    idempotency_key: str,
) -> bool:
    """Queue one template message to a member (WhatsApp first, SMS fallback when undeliverable),
    delivered after commit. Needs the member's consent. Idempotent per chama on the key."""
    return queue_message(chama_id, member_id, template, params, idempotency_key)
