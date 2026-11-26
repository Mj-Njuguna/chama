"""Late fines and two-official fine waivers (internal).

Assessment: on the first Nairobi day after a contribution's grace ends (the nightly job runs just
after midnight), a contribution with a balance gets ONE fine, per the constitution: a flat amount,
or a percentage of the unpaid balance (rounded half up to the cent). The fine is a new
obligation and a ledger posting Dr ``fines_receivable`` / Cr ``fine_income`` keyed
``fine:{fine_id}``. Claims use ``FOR UPDATE SKIP LOCKED`` and the one-fine-per-contribution
constraint, so a rerun or a concurrent run never fines twice.

Waivers: one official requests (reason), a DIFFERENT official approves; neither can be the fined
member. Approval posts Dr ``fine_income`` / Cr ``fines_receivable`` for the fine's open balance
(``waiver:{id}``) and marks the fine waived. Both steps need a fresh server PIN (policy).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.contributions.cycles import today
from apps.contributions.models import (
    FineWaiver,
    Obligation,
    ObligationKind,
    ObligationStatus,
    WaiverStatus,
)
from apps.ledger.services import LedgerAccount, Line, TransactionKind, post
from apps.organizations.services import rules_for_chama
from core.api_errors import ApiError, state_conflict


def fine_amount(rules: dict[str, Any], balance_minor: int) -> int:
    """Flat amount, or the percentage of the unpaid balance rounded half up to the cent."""
    penalty = str(rules["penalty_type"])
    if penalty == "flat":
        return int(rules["penalty_flat_minor"])
    if penalty == "percent":
        rate = Decimal(int(rules["penalty_rate_bp"])) / Decimal(10000)
        return int((Decimal(balance_minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return 0


@dataclass(frozen=True)
class FineIssued:
    fine_id: uuid.UUID
    member_id: uuid.UUID
    amount_minor: int


def assess_fines(chama_id: uuid.UUID, on: date | None = None) -> list[FineIssued]:
    """Fine every contribution whose grace ended before ``on`` and still has a balance."""
    on = on or today()
    rules = rules_for_chama(chama_id)
    issued: list[FineIssued] = []
    with transaction.atomic():
        late = (
            Obligation.objects.select_for_update(skip_locked=True)
            .filter(
                kind=ObligationKind.CONTRIBUTION,
                status=ObligationStatus.OPEN,
                grace_until__lt=on,
            )
            .exclude(
                id__in=Obligation.objects.filter(kind=ObligationKind.FINE).values(
                    "fined_obligation_id"
                )
            )
            .order_by("due_date", "id")
        )
        for row in late:
            amount = fine_amount(rules, row.balance_minor)
            if amount <= 0:
                continue
            try:
                with transaction.atomic():
                    fine = Obligation.objects.create(
                        chama_id=chama_id,
                        member_id=row.member_id,
                        kind=ObligationKind.FINE,
                        fined_obligation_id=row.id,
                        due_date=on,
                        grace_until=on,
                        amount_minor=amount,
                    )
                    post(
                        chama_id,
                        TransactionKind.FINE_ASSESSED,
                        f"fine:{fine.id}",
                        [
                            Line(LedgerAccount.FINES_RECEIVABLE, amount, row.member_id),
                            Line(LedgerAccount.FINE_INCOME, -amount),
                        ],
                        member_id=row.member_id,
                        memo="late contribution",
                    )
            except IntegrityError:  # fined concurrently: nothing to do
                continue
            issued.append(FineIssued(fine.id, row.member_id, amount))
    return issued


# ---------------------------------------------------------------- waivers


@dataclass(frozen=True)
class WaiverView:
    waiver_id: uuid.UUID
    obligation_id: uuid.UUID
    member_id: uuid.UUID
    reason: str
    status: str
    requested_by_member_id: uuid.UUID
    approved_by_member_id: uuid.UUID | None
    amount_minor: int
    created_at: datetime
    decided_at: datetime | None


def _view(w: FineWaiver) -> WaiverView:
    return WaiverView(
        w.id,
        w.obligation_id,
        w.member_id,
        w.reason,
        w.status,
        w.requested_by_member_id,
        w.approved_by_member_id,
        w.amount_minor or 0,
        w.created_at,
        w.decided_at,
    )


def _not_found() -> ApiError:
    return ApiError("not_found", "No such fine or waiver.", status_code=404)


def request_waiver(
    chama_id: uuid.UUID, obligation_id: uuid.UUID, requested_by: uuid.UUID, reason: str
) -> WaiverView:
    with transaction.atomic():
        fine = (
            Obligation.objects.select_for_update()
            .filter(id=obligation_id, kind=ObligationKind.FINE)
            .first()
        )
        if fine is None:
            raise _not_found()
        if fine.status != ObligationStatus.OPEN or fine.balance_minor <= 0:
            raise state_conflict("fine_not_open", "This fine is already paid or waived.")
        if FineWaiver.objects.filter(obligation_id=fine.id, status=WaiverStatus.PENDING).exists():
            raise state_conflict("waiver_pending", "A waiver for this fine is already pending.")
        waiver = FineWaiver.objects.create(
            chama_id=chama_id,
            obligation_id=fine.id,
            member_id=fine.member_id,
            reason=reason[:200],
            requested_by_member_id=requested_by,
            amount_minor=fine.balance_minor,
        )
    return _view(waiver)


def approve_waiver(chama_id: uuid.UUID, waiver_id: uuid.UUID, approver: uuid.UUID) -> WaiverView:
    with transaction.atomic():
        waiver = FineWaiver.objects.select_for_update().filter(id=waiver_id).first()
        if waiver is None:
            raise _not_found()
        if waiver.status != WaiverStatus.PENDING:
            raise state_conflict("waiver_decided", "This waiver was already decided.")
        if approver in (waiver.requested_by_member_id, waiver.member_id):
            raise state_conflict(
                "same_official",
                "A different official must approve: not the requester or the fined member.",
            )
        fine = Obligation.objects.select_for_update().get(id=waiver.obligation_id)
        amount = fine.balance_minor
        if amount > 0:
            post(
                chama_id,
                TransactionKind.FINE_WAIVED,
                f"waiver:{waiver.id}",
                [
                    Line(LedgerAccount.FINE_INCOME, amount),
                    Line(LedgerAccount.FINES_RECEIVABLE, -amount, fine.member_id),
                ],
                member_id=fine.member_id,
                memo="fine waived",
            )
            Obligation.objects.filter(id=fine.id).update(
                waived_minor=F("waived_minor") + amount, status=ObligationStatus.WAIVED
            )
        waiver.status = WaiverStatus.APPROVED
        waiver.approved_by_member_id = approver
        waiver.amount_minor = amount
        waiver.decided_at = timezone.now()
        waiver.save(update_fields=["status", "approved_by_member_id", "amount_minor", "decided_at"])
        if amount > 0:
            from apps.notifications.services import publish_feed_event

            publish_feed_event(
                chama_id,
                "fine_waived",
                {"member_id": str(fine.member_id), "amount_minor": amount},
                key=f"waiver:{waiver.id}",
            )
    return _view(waiver)


def waivers(status: str | None = None) -> list[WaiverView]:
    rows = FineWaiver.objects.order_by("-created_at", "-id")
    if status:
        rows = rows.filter(status=status)
    return [_view(w) for w in rows[:100]]


def pending_waiver_count() -> int:
    return FineWaiver.objects.filter(status=WaiverStatus.PENDING).count()
