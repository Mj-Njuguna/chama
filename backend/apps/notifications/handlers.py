"""Event subscribers (internal; registered in ``NotificationsConfig.ready``)."""

import uuid

from apps.notifications.feed import notify_push, publish_feed_event
from apps.notifications.messaging import queue_message
from apps.notifications.text import format_kes
from apps.organizations.services import chama_name, member_names
from apps.payments.events import PaymentAllocated, PaymentConfirmed
from core.tenancy import chama_context


def on_payment_confirmed(event: PaymentConfirmed) -> None:
    """Feed entry for the chama; receipt message and push for the member when known."""
    _payment(event.chama_id, event.payment_id, event.member_id, event.amount_minor, "")


def on_payment_allocated(event: PaymentAllocated) -> None:
    """An official assigned an unallocated payment: a feed entry naming the member, and the
    receipt the member could not get before (still one receipt per payment)."""
    _payment(event.chama_id, event.payment_id, event.member_id, event.amount_minor, ":allocated")


def _payment(
    chama_id: uuid.UUID,
    payment_id: uuid.UUID,
    member_id: uuid.UUID | None,
    amount_minor: int,
    key_suffix: str,
) -> None:
    with chama_context(chama_id):
        name = member_names([member_id]).get(member_id, "") if member_id else ""
        publish_feed_event(
            chama_id,
            "payment_confirmed",
            {
                "payment_id": str(payment_id),
                "member_id": str(member_id) if member_id else None,
                "amount_minor": amount_minor,
                "member_name": name or None,
            },
            key=f"payment:{payment_id}{key_suffix}",
        )
        if member_id is None:
            return
        queue_message(
            chama_id,
            member_id,
            "payment_receipt",
            [chama_name(chama_id), format_kes(amount_minor), str(payment_id)[:8]],
            f"payment:{payment_id}:receipt",
        )
        notify_push(chama_id, [member_id], "payment_confirmed", {"payment_id": payment_id})
