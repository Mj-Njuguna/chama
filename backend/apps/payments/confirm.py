"""Recording confirmed payments, processing callbacks, and the unallocated resolver (internal).

THE payment confirmation transaction (``record_payment``), used by every source:

1. insert the Payment (a repeated M-Pesa receipt for the chama records nothing),
2. with a known member: ``contributions.services.allocate`` (fines, loan installments,
   contributions, then savings) debiting ``cash_mpesa``; without one: Dr ``cash_mpesa`` /
   Cr ``unallocated`` for the resolver,
3. publish ``PaymentConfirmed`` after commit.

Member matching is automatic only on a valid member code typed as the paybill account number,
or on the STK request that asked for the payment. A till payment carries no account number, so
it always waits in the resolver. Masked payer numbers and near-miss codes are SUGGESTIONS for an
official, never an automatic assignment.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.services import phones_for
from apps.contributions.services import allocate
from apps.ledger.services import LedgerAccount, Line, TransactionKind, post
from apps.organizations.services import (
    active_member_by_code,
    is_active_member,
    is_valid_member_code,
    member_directory,
    normalize_member_code,
)
from apps.payments.events import PaymentAllocated, PaymentConfirmed
from apps.payments.gateway import StkResult
from apps.payments.models import (
    CallbackEvent,
    LinkingChannel,
    LinkingStatus,
    Payment,
    PaymentAllocation,
    PaymentLinking,
    PaymentSource,
    StkRequest,
    StkStatus,
)
from core.api_errors import state_conflict
from core.events import publish

_RECEIPT_CONSTRAINT = "payments_payment_receipt_uniq"
_RECEIPT = re.compile(r"^[A-Z0-9]{6,32}$")


@dataclass(frozen=True)
class Recorded:
    payment_id: uuid.UUID
    created: bool
    member_id: uuid.UUID | None


def _constraint(error: IntegrityError) -> str | None:
    diag = getattr(getattr(error, "__cause__", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def record_payment(
    chama_id: uuid.UUID,
    *,
    source: str,
    mpesa_receipt: str,
    amount_minor: int,
    received_at: datetime,
    member_id: uuid.UUID | None,
    bill_ref: str = "",
    payer_masked: str = "",
    payer_name: str = "",
    stk_request_id: uuid.UUID | None = None,
) -> Recorded:
    with transaction.atomic():
        try:
            with transaction.atomic():
                payment = Payment.objects.create(
                    chama_id=chama_id,
                    source=source,
                    mpesa_receipt=mpesa_receipt,
                    amount_minor=amount_minor,
                    received_at=received_at,
                    bill_ref=bill_ref[:40],
                    payer_masked=payer_masked[:40],
                    payer_name=payer_name[:100],
                    stk_request_id=stk_request_id,
                )
        except IntegrityError as error:
            if _constraint(error) != _RECEIPT_CONSTRAINT:
                raise
            existing = Payment.objects.get(mpesa_receipt=mpesa_receipt)
            owner = (
                PaymentAllocation.objects.filter(payment_id=existing.id)
                .values_list("member_id", flat=True)
                .first()
            )
            return Recorded(existing.id, False, owner)
        if member_id is not None:
            allocate(
                chama_id,
                member_id=member_id,
                amount_minor=amount_minor,
                source_key=f"payment:{payment.id}",
                debit_account=LedgerAccount.CASH_MPESA,
            )
            PaymentAllocation.objects.create(
                chama_id=chama_id, payment_id=payment.id, member_id=member_id
            )
        else:
            post(
                chama_id,
                TransactionKind.PAYMENT,
                f"payment:{payment.id}:unallocated",
                [
                    Line(LedgerAccount.CASH_MPESA, amount_minor),
                    Line(LedgerAccount.UNALLOCATED, -amount_minor),
                ],
                memo=f"mpesa {mpesa_receipt}",
            )
        publish(PaymentConfirmed(chama_id, payment.id, member_id, amount_minor))
    return Recorded(payment.id, True, member_id)


# ---------------------------------------------------------------- C2B confirmations


def _amount_minor(raw: object) -> int | None:
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    minor = value * 100
    if value <= 0 or minor != minor.to_integral_value():
        return None
    return int(minor)


def _received_at(raw: object, fallback: datetime) -> datetime:
    """TransTime as YYYYMMDDHHMMSS in Nairobi time (format to be confirmed in the sandbox);
    anything else falls back to when the callback arrived."""
    if isinstance(raw, str) and re.fullmatch(r"\d{14}", raw):
        try:
            local = datetime.strptime(raw, "%Y%m%d%H%M%S")  # noqa: DTZ007  (zone added below)
        except ValueError:
            return fallback
        return local.replace(tzinfo=ZoneInfo(settings.BUSINESS_TIME_ZONE))
    return fallback


def _text(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    return value.strip() if isinstance(value, str) else ""


def process_c2b_confirmation(event: CallbackEvent) -> str:
    """Record the payment a C2B confirmation reports. Returns the outcome stored on the event.
    Only CONFIRMED C2B fields are read."""
    body = event.body if isinstance(event.body, dict) else {}
    receipt = _text(body, "TransID").upper()
    amount = _amount_minor(body.get("TransAmount"))
    if not _RECEIPT.match(receipt) or amount is None:
        return "invalid_body"
    linking = PaymentLinking.objects.filter(status=LinkingStatus.ACTIVE).first()
    if linking is None:
        return "not_linked"
    if _text(body, "BusinessShortCode") != linking.shortcode:
        return "shortcode_mismatch"
    bill_ref = _text(body, "BillRefNumber")
    member_id = None
    if linking.channel == LinkingChannel.PAYBILL and bill_ref:
        code = normalize_member_code(bill_ref)
        if is_valid_member_code(code):
            member_id = active_member_by_code(code)
    recorded = record_payment(
        event.chama_id,
        source=PaymentSource.C2B,
        mpesa_receipt=receipt,
        amount_minor=amount,
        received_at=_received_at(body.get("TransTime"), event.received_at),
        member_id=member_id,
        bill_ref=bill_ref,
        payer_masked=_text(body, "MSISDN"),
        payer_name=_text(body, "FirstName"),
    )
    if not recorded.created:
        return "duplicate"
    return "allocated" if member_id else "unallocated"


# ---------------------------------------------------------------- STK results


def apply_stk_result(result: StkResult) -> str:
    """Finish the STK request a result belongs to. A success always records the payment, even
    when the request had already been marked as timed out (the money has moved)."""
    with transaction.atomic():
        request = (
            StkRequest.objects.select_for_update()
            .filter(checkout_request_id=result.checkout_request_id)
            .first()
        )
        if request is None:
            return "unknown_request"
        if request.status == StkStatus.SUCCEEDED:
            return "duplicate"
        now = timezone.now()
        if result.outcome == StkStatus.SUCCEEDED:
            receipt = (result.mpesa_receipt or "").upper()
            if not _RECEIPT.match(receipt):
                return "invalid_body"
            recorded = record_payment(
                request.chama_id,
                source=PaymentSource.STK,
                mpesa_receipt=receipt,
                amount_minor=result.amount_minor or request.amount_minor,
                received_at=now,
                member_id=request.member_id,
                stk_request_id=request.id,
            )
            request.payment_id = recorded.payment_id
        elif request.status != StkStatus.PENDING:
            return "already_final"
        request.status = result.outcome if result.outcome in StkStatus.values else StkStatus.FAILED
        request.result_code = result.result_code[:16]
        request.completed_at = now
        request.save(update_fields=["status", "result_code", "completed_at", "payment_id"])
    return str(request.status)


# ---------------------------------------------------------------- resolver


@dataclass(frozen=True)
class Suggestion:
    member_id: uuid.UUID
    reason: str  # similar_code | phone_pattern


def _one_off(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) == 1


def _mask_parts(masked: str) -> tuple[str, str] | None:
    """``"2547 *** 126"`` -> ("2547", "126"). None unless both ends are visible."""
    if "*" not in masked:
        return None
    groups = re.findall(r"\d+", masked)
    if len(groups) < 2 or len(groups[-1]) < 3:
        return None
    return groups[0], groups[-1]


def suggestions_for(payment: Payment) -> list[Suggestion]:
    directory = member_directory()
    found: list[Suggestion] = []
    code = normalize_member_code(payment.bill_ref)
    if code:
        found += [
            Suggestion(m.member_id, "similar_code")
            for m in directory
            if _one_off(code, m.member_code)
        ]
    parts = _mask_parts(payment.payer_masked)
    if parts is not None:
        prefix, suffix = parts
        phones = phones_for([m.user_id for m in directory])
        for m in directory:
            digits = re.sub(r"\D", "", phones.get(m.user_id, ""))
            matches = digits.startswith(prefix) and digits.endswith(suffix)
            if matches and all(s.member_id != m.member_id for s in found):
                found.append(Suggestion(m.member_id, "phone_pattern"))
    return found[:5]


def allocated_member(payment_id: uuid.UUID) -> uuid.UUID | None:
    return (
        PaymentAllocation.objects.filter(payment_id=payment_id)
        .values_list("member_id", flat=True)
        .first()
    )


def resolve_payment(
    chama_id: uuid.UUID,
    payment_id: uuid.UUID,
    member_id: uuid.UUID,
    allocated_by_member_id: uuid.UUID,
) -> Payment:
    """Assign an unallocated payment to a member. Repeating the same assignment is a no-op;
    a different member for an allocated payment is a conflict."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(id=payment_id).first()
        if payment is None:
            raise LookupError(payment_id)
        owner = allocated_member(payment.id)
        if owner is not None:
            if owner == member_id:
                return payment
            raise state_conflict("payment_already_allocated", "This payment already has a member.")
        if not is_active_member(member_id):
            raise state_conflict("member_not_active", "The member is not active in this chama.")
        allocate(
            chama_id,
            member_id=member_id,
            amount_minor=payment.amount_minor,
            source_key=f"payment:{payment.id}",
            debit_account=LedgerAccount.UNALLOCATED,
        )
        PaymentAllocation.objects.create(
            chama_id=chama_id,
            payment_id=payment.id,
            member_id=member_id,
            allocated_by_member_id=allocated_by_member_id,
        )
        publish(PaymentAllocated(chama_id, payment.id, member_id, payment.amount_minor))
    return payment
