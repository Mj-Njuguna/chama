"""Fake OTP senders and the dev-only outbox (dev and test settings only).

Accepted messages go to a Redis "outbox" instead of a phone, kept for
``FAKE_OTP_OUTBOX_TTL_SECONDS``: developers read codes at ``GET /dev/otp-outbox?phone=...``
(dev only), and tests read them with :func:`outbox`. Production settings configure no fake, and
the ``notifications.E001`` system check refuses one if it were configured.

Tests can make a channel fail for a number through :data:`BEHAVIOUR`.
"""

import json
import time
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from apps.notifications.otp import OtpChannel, SendOutcome
from core.redis import get_redis

OUTBOX_PREFIX = "fake-otp-outbox:"
OUTBOX_KEEP = 10

# (channel, phone) -> "accepted" | "undeliverable" | "failed" | "raise" | "slow". Tests only.
BEHAVIOUR: dict[tuple[OtpChannel, str], str] = {}


def _key(phone_e164: str) -> str:
    return OUTBOX_PREFIX + phone_e164


class _FakeSender:
    channel: OtpChannel

    def send_otp(self, phone_e164: str, code: str, *, timeout: float) -> SendOutcome:
        behaviour = BEHAVIOUR.get((self.channel, phone_e164), "accepted")
        if behaviour == "raise":
            raise RuntimeError("fake provider error")
        if behaviour == "slow":
            # A hung provider: the caller stops waiting at the timeout. It never writes, so the
            # leftover thread cannot touch a later test's outbox. (A real provider might still
            # deliver late: a failed send invalidates its challenge.)
            time.sleep(timeout + 0.5)
            return SendOutcome.FAILED
        if behaviour in ("undeliverable", "failed"):
            return SendOutcome(behaviour)
        message = {"channel": self.channel.value, "code": code, "sent_at": _now()}
        client = get_redis()
        pipe = client.pipeline()
        pipe.lpush(_key(phone_e164), json.dumps(message))
        pipe.ltrim(_key(phone_e164), 0, OUTBOX_KEEP - 1)
        pipe.expire(_key(phone_e164), settings.FAKE_OTP_OUTBOX_TTL_SECONDS)
        pipe.execute()
        return SendOutcome.ACCEPTED


class FakeWhatsAppSender(_FakeSender):
    channel = OtpChannel.WHATSAPP


class FakeSmsSender(_FakeSender):
    channel = OtpChannel.SMS


def _now() -> str:
    return datetime.now(UTC).isoformat()


def outbox(phone_e164: str) -> list[dict[str, Any]]:
    """Messages sent to ``phone_e164``, newest first."""
    return [json.loads(raw) for raw in get_redis().lrange(_key(phone_e164), 0, -1)]


def clear_outbox() -> None:
    client = get_redis()
    for key in client.scan_iter(match=OUTBOX_PREFIX + "*"):
        client.delete(key)


# ---------------------------------------------------------------- push (no sending yet)

# (token_type, token, message) for every push the fake accepted. Tests only; never persisted.
PUSHED: list[tuple[str, str, Any]] = []


class FakePushProvider:
    """Accepts every message and keeps it in :data:`PUSHED` (dev and test only)."""

    def send(self, token_type: str, token: str, message: Any) -> Any:
        from apps.notifications.push import PushOutcome

        PUSHED.append((token_type, token, message))
        return PushOutcome.ACCEPTED


# ---------------------------------------------------------------- member messages (fakes)

MESSAGE_OUTBOX_PREFIX = "fake-message-outbox:"
# (channel, phone) -> "accepted" | "undeliverable" | "failed". Tests only.
MESSAGE_BEHAVIOUR: dict[tuple[str, str], str] = {}


class _FakeMessenger:
    channel = ""

    def send_template(
        self, phone_e164: str, template: str, params: list[str], *, timeout: float
    ) -> "Any":
        from apps.notifications.messaging import Outcome, SendResult

        behaviour = MESSAGE_BEHAVIOUR.get((self.channel, phone_e164), "accepted")
        if behaviour == "undeliverable":
            return SendResult(Outcome.UNDELIVERABLE, error_code="131026")
        if behaviour == "failed":
            return SendResult(Outcome.FAILED, error_code="fake")
        provider_id = f"fake.{self.channel}.{time.time_ns()}"
        entry = {
            "channel": self.channel,
            "template": template,
            "params": params,
            "provider_message_id": provider_id,
            "sent_at": _now(),
        }
        client = get_redis()
        key = MESSAGE_OUTBOX_PREFIX + phone_e164
        client.lpush(key, json.dumps(entry))
        client.ltrim(key, 0, OUTBOX_KEEP - 1)
        client.expire(key, settings.FAKE_OTP_OUTBOX_TTL_SECONDS)
        return SendResult(Outcome.ACCEPTED, provider_message_id=provider_id)


class FakeWhatsAppMessenger(_FakeMessenger):
    channel = "whatsapp"


class FakeSmsMessenger(_FakeMessenger):
    channel = "sms"


def message_outbox(phone_e164: str) -> list[dict[str, Any]]:
    """Messages the fakes accepted for ``phone_e164``, newest first."""
    raw = get_redis().lrange(MESSAGE_OUTBOX_PREFIX + phone_e164, 0, -1)
    return [json.loads(item) for item in raw]
