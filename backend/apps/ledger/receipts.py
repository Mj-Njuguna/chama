"""Manual receipts: money an official records by hand (internal).

The receipt row and its ledger posting commit together. A receipt for a member credits their
savings for now; when contribution obligations, fines and loans exist, the payment allocation
in ``contributions`` decides where it goes. A receipt without a member lands in ``unallocated``
for the resolver. Both rows are append-only: a mistake is corrected by a reversal.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.ledger.models import LedgerAccount, Receipt, ReceiptChannel, TransactionKind
from apps.ledger.services import Line, post
from apps.organizations.services import is_active_member
from core.api_errors import state_conflict

_REFERENCE_CONSTRAINT = "ledger_receipt_reference_uniq"
_REFERENCE = re.compile(r"^[A-Z0-9/-]{3,40}$")
# Upper bound for one receipt: KES 10,000,000.
RECEIPT_MAX_MINOR = 1_000_000_000

_CASH = {
    ReceiptChannel.BANK: LedgerAccount.CASH_BANK,
    ReceiptChannel.MPESA: LedgerAccount.CASH_MPESA,
}


@dataclass(frozen=True)
class ReceiptView:
    receipt_id: uuid.UUID
    channel: str
    reference: str
    amount_minor: int
    received_on: date
    member_id: uuid.UUID | None
    note: str
    recorded_by_member_id: uuid.UUID
    created_at: datetime


def normalize_reference(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def _today() -> date:
    return datetime.now(ZoneInfo(settings.BUSINESS_TIME_ZONE)).date()


def record_receipt(
    *,
    chama_id: uuid.UUID,
    recorded_by_member_id: uuid.UUID,
    channel: str,
    reference: str,
    amount_minor: int,
    received_on: date,
    member_id: uuid.UUID | None,
    note: str = "",
) -> ReceiptView:
    """Record one receipt and post it. Raises ValidationError or a state conflict."""
    ref = normalize_reference(reference)
    errors: dict[str, list[str]] = {}
    if channel not in ReceiptChannel.values:
        errors["channel"] = ["invalid"]
    if not _REFERENCE.match(ref):
        errors["reference"] = ["invalid"]
    if not 0 < amount_minor <= RECEIPT_MAX_MINOR:
        errors["amount_minor"] = ["out_of_range"]
    if received_on > _today():
        errors["received_on"] = ["in_future"]
    if errors:
        raise ValidationError(errors)
    if member_id is not None and not is_active_member(member_id):
        raise state_conflict("member_not_active", "The member is not active in this chama.")

    cash = _CASH[ReceiptChannel(channel)]
    credit = (
        Line(LedgerAccount.MEMBER_SAVINGS, -amount_minor, member_id)
        if member_id is not None
        else Line(LedgerAccount.UNALLOCATED, -amount_minor)
    )
    try:
        with transaction.atomic():
            receipt = Receipt.objects.create(
                chama_id=chama_id,
                channel=channel,
                reference=ref,
                amount_minor=amount_minor,
                received_on=received_on,
                member_id=member_id,
                note=note[:200],
                recorded_by_member_id=recorded_by_member_id,
            )
            post(
                chama_id,
                TransactionKind.RECEIPT,
                f"receipt:{receipt.id}",
                [Line(cash, amount_minor), credit],
                member_id=member_id,
                memo=f"{channel} {ref}",
            )
    except IntegrityError as error:
        diag = getattr(getattr(error, "__cause__", None), "diag", None)
        if getattr(diag, "constraint_name", None) == _REFERENCE_CONSTRAINT:
            raise state_conflict(
                "duplicate_receipt", "A receipt with this reference was already recorded."
            ) from error
        raise
    return _view(receipt)


def recent_receipts(limit: int = 50) -> list[ReceiptView]:
    return [_view(r) for r in Receipt.objects.order_by("-created_at", "-id")[:limit]]


def _view(r: Receipt) -> ReceiptView:
    return ReceiptView(
        r.id,
        r.channel,
        r.reference,
        r.amount_minor,
        r.received_on,
        r.member_id,
        r.note,
        r.recorded_by_member_id,
        r.created_at,
    )
