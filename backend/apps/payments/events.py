"""Domain events published by ``payments``: names and payload dataclasses.

Public to other modules. Published with ``core.events.publish`` (delivered after commit).
"""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentConfirmed:
    """A new M-Pesa payment was recorded. ``member_id`` is None when it landed in the
    unallocated resolver."""

    chama_id: uuid.UUID
    payment_id: uuid.UUID
    member_id: uuid.UUID | None
    amount_minor: int


@dataclass(frozen=True)
class PaymentAllocated:
    """An official assigned an unallocated payment to a member (the resolver)."""

    chama_id: uuid.UUID
    payment_id: uuid.UUID
    member_id: uuid.UUID
    amount_minor: int
