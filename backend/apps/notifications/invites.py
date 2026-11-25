"""Invitation messages: a person was invited to a chama. Internal to notifications.

Only the fake sender exists until the WhatsApp Cloud API and SMS provider details are verified.
Without a configured sender the invite still exists in the app (the invitee sees it after signing
in with the invited number); ``send_invite`` then reports ``sent=False``. The message carries the
chama name and the member reference code only, never amounts.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from django.conf import settings
from django.utils.module_loading import import_string

from core.logging import get_logger
from core.redis import get_redis

log = get_logger(__name__)

INVITE_OUTBOX_PREFIX = "fake-invite-outbox:"


@dataclass(frozen=True)
class InviteSendResult:
    sent: bool
    channel: str = ""


class InviteSender(Protocol):
    def send_invite(self, phone_e164: str, chama_name: str, member_code: str) -> str: ...


def send_invite(phone_e164: str, chama_name: str, member_code: str) -> InviteSendResult:
    path = settings.INVITE_SENDER
    if not path:
        return InviteSendResult(sent=False)
    try:
        channel = import_string(path)().send_invite(phone_e164, chama_name, member_code)
    except Exception:  # a failed message never fails the invitation itself
        log.warning("invite_send_failed")
        return InviteSendResult(sent=False)
    return InviteSendResult(sent=True, channel=channel)


class FakeInviteSender:
    """Dev and test only: keeps the message in a Redis outbox instead of sending it."""

    def send_invite(self, phone_e164: str, chama_name: str, member_code: str) -> str:
        message = {
            "chama": chama_name,
            "member_code": member_code,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        key = INVITE_OUTBOX_PREFIX + phone_e164
        client = get_redis()
        client.lpush(key, json.dumps(message))
        client.expire(key, settings.FAKE_OTP_OUTBOX_TTL_SECONDS)
        return "whatsapp"


def invite_outbox(phone_e164: str) -> list[dict[str, Any]]:
    return [json.loads(raw) for raw in get_redis().lrange(INVITE_OUTBOX_PREFIX + phone_e164, 0, -1)]
