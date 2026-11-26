"""Member messages: queueing, the channel router and provider interfaces (internal).

``queue_message`` records a ``Message`` in the caller's transaction (unique per chama on its
idempotency key) and hands delivery to Celery after commit. Delivery:

1. claim the queued message (``queued`` -> ``sending``) in a short chama transaction;
2. call the providers OUTSIDE any transaction: WhatsApp first; SMS only when WhatsApp reports the
   member undeliverable (the confirmed codes in :mod:`apps.notifications.whatsapp`), never on
   other failures;
3. record the outcome. A later WhatsApp status webhook that reports an undeliverable failure
   sends the SMS fallback the same way.

Consent: a member who has not accepted the chama's terms gets nothing (``skipped``). Messages
carry only what the member may see about themselves; never other members' data.
"""

import enum
import uuid
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.module_loading import import_string

from apps.notifications.models import Message, MessageStatus, ProviderMessageRef
from core.logging import get_logger
from core.tenancy import chama_context

log = get_logger(__name__)

# Template name -> number of positional body parameters (the approved WhatsApp template and the
# SMS text below must match).
TEMPLATES: dict[str, int] = {
    "member_invite": 2,  # chama name, member code
    "contribution_reminder": 3,  # chama name, amount, due date
    "payment_receipt": 3,  # chama name, amount, M-Pesa or slip reference
    "fine_notice": 2,  # chama name, amount
    "guarantor_request": 3,  # chama name, borrower name, amount (defined; used from phase 3)
}

SMS_TEXT: dict[str, str] = {
    "member_invite": "You are invited to {0} on Chama. Your member code is {1}.",
    "contribution_reminder": "{0}: your contribution of {1} is due on {2}.",
    "payment_receipt": "{0}: we received {1} (ref {2}). Thank you.",
    "fine_notice": "{0}: a late fine of {1} was added to your account.",
    "guarantor_request": "{0}: {1} asked you to guarantee a loan of {2}. Open the app to reply.",
}

CHANNEL_ORDER = ("whatsapp", "sms")


class Outcome(enum.StrEnum):
    ACCEPTED = "accepted"
    UNDELIVERABLE = "undeliverable"  # this channel cannot reach the member: try the next one
    FAILED = "failed"  # provider or request problem: stop


@dataclass(frozen=True)
class SendResult:
    outcome: Outcome
    provider_message_id: str = ""
    error_code: str = ""


class MessageProvider(Protocol):
    """Sends one template message to one E.164 number. Must not log the number."""

    def send_template(
        self, phone_e164: str, template: str, params: list[str], *, timeout: float
    ) -> SendResult: ...


def provider_for(channel: str) -> MessageProvider | None:
    path = settings.MESSAGE_SENDERS.get(channel, "")
    if not path:
        return None
    provider: MessageProvider = import_string(path)()
    return provider


def queue_message(
    chama_id: uuid.UUID,
    member_id: uuid.UUID,
    template: str,
    params: list[str],
    idempotency_key: str,
) -> bool:
    """Record one message and deliver it after commit. False when it already exists (same key)
    or the member has not consented (recorded as skipped)."""
    if template not in TEMPLATES or len(params) != TEMPLATES[template]:
        raise ValueError(f"template {template!r} needs {TEMPLATES.get(template)} parameters")
    from apps.organizations.services import member_contacts

    contact = member_contacts([member_id]).get(member_id)
    status = MessageStatus.QUEUED if contact and contact.consented else MessageStatus.SKIPPED
    try:
        with transaction.atomic():
            message = Message.objects.create(
                chama_id=chama_id,
                member_id=member_id,
                template=template,
                params=[str(p) for p in params],
                idempotency_key=idempotency_key,
                status=status,
                error_code="" if status == MessageStatus.QUEUED else "no_consent",
            )
    except IntegrityError:
        return False
    if status != MessageStatus.QUEUED:
        return False
    from apps.notifications.tasks import deliver_message

    transaction.on_commit(
        lambda: deliver_message.delay(str(chama_id), str(message.id), CHANNEL_ORDER[0])
    )
    return True


def deliver(chama_id: uuid.UUID, message_id: uuid.UUID, first_channel: str = "whatsapp") -> str:
    """Claim, send and record one message. Returns the final status."""
    from apps.organizations.services import member_contacts

    with chama_context(chama_id):
        message = (
            Message.objects.select_for_update(skip_locked=True)
            .filter(id=message_id, status=MessageStatus.QUEUED)
            .first()
        )
        if message is None:
            return "not_claimed"
        contact = member_contacts([message.member_id]).get(message.member_id)
        message.status = MessageStatus.SENDING
        message.attempts += 1
        message.save(update_fields=["status", "attempts", "updated_at"])
        template, params = message.template, list(message.params)

    status, channel, provider_id, error = MessageStatus.SKIPPED, "", "", "no_channel"
    if contact is not None and contact.consented:
        channels = CHANNEL_ORDER[CHANNEL_ORDER.index(first_channel) :]
        for channel_name in channels:
            provider = provider_for(channel_name)
            if provider is None:
                continue
            channel = channel_name
            try:
                result = provider.send_template(
                    contact.phone,
                    template,
                    params,
                    timeout=settings.MESSAGING_PROVIDER_TIMEOUT_SECONDS,
                )
            except Exception:  # deliberately broad: a provider error never escapes
                log.warning("message_send_failed", channel=channel_name)
                result = SendResult(Outcome.FAILED, error_code="exception")
            provider_id, error = result.provider_message_id, result.error_code
            if result.outcome == Outcome.ACCEPTED:
                status = MessageStatus.SENT
                break
            status = (
                MessageStatus.UNDELIVERABLE
                if result.outcome == Outcome.UNDELIVERABLE
                else MessageStatus.FAILED
            )
            if result.outcome == Outcome.FAILED:
                break
    elif contact is None or not contact.consented:
        error = "no_consent"

    with chama_context(chama_id):
        Message.objects.filter(id=message_id).update(
            status=status, channel=channel, provider_message_id=provider_id, error_code=error
        )
        if provider_id and status == MessageStatus.SENT:
            ProviderMessageRef.objects.get_or_create(
                provider_message_id=provider_id,
                defaults={"chama_id": chama_id, "message_id": message_id},
            )
    return str(status)


def apply_provider_status(provider_message_id: str, status: str, error_code: str) -> None:
    """A WhatsApp status update (delivered, read, failed). An undeliverable failure on WhatsApp
    re-queues the message for the SMS fallback."""
    ref = ProviderMessageRef.objects.filter(provider_message_id=provider_message_id).first()
    if ref is None:
        return
    from apps.notifications.whatsapp import UNDELIVERABLE_CODES

    fallback = False
    with chama_context(ref.chama_id):
        message = Message.objects.select_for_update().filter(id=ref.message_id).first()
        if message is None or message.channel != "whatsapp":
            return
        if status in ("delivered", "read") and message.status in (
            MessageStatus.SENT,
            MessageStatus.DELIVERED,
        ):
            message.status = MessageStatus(status)
            message.save(update_fields=["status", "updated_at"])
        elif status == "failed" and message.status in (
            MessageStatus.SENT,
            MessageStatus.DELIVERED,
        ):
            message.error_code = error_code
            if error_code.isdigit() and int(error_code) in UNDELIVERABLE_CODES:
                message.status = MessageStatus.QUEUED
                fallback = True
            else:
                message.status = MessageStatus.FAILED
            message.save(update_fields=["status", "error_code", "updated_at"])
    if fallback:
        from apps.notifications.tasks import deliver_message

        deliver_message.delay(str(ref.chama_id), str(ref.message_id), "sms")
