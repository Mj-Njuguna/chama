"""Read models over cycles and obligations (internal; RLS-scoped to the current chama)."""

import uuid
from dataclasses import dataclass
from datetime import date

from django.db.models import Q, Sum

from apps.contributions.cycles import today
from apps.contributions.models import Cycle, Obligation, ObligationKind, ObligationStatus


def current_cycle(on: date | None = None) -> Cycle | None:
    """The latest cycle that has started, else the earliest upcoming one."""
    on = on or today()
    started = Cycle.objects.filter(period_start__lte=on).order_by("-period_start").first()
    return started or Cycle.objects.order_by("period_start").first()


@dataclass(frozen=True)
class MemberCycle:
    status: str  # paid | due | overdue | none
    due_date: date | None
    balance_minor: int | None


def member_cycle(member_id: uuid.UUID, on: date | None = None) -> MemberCycle:
    on = on or today()
    cycle = current_cycle(on)
    if cycle is None:
        return MemberCycle("none", None, None)
    row = Obligation.objects.filter(
        cycle_id=cycle.id, member_id=member_id, kind=ObligationKind.CONTRIBUTION
    ).first()
    if row is None:
        return MemberCycle("none", cycle.due_date, None)
    balance = row.balance_minor
    if balance <= 0:
        status = "paid"
    elif on > row.grace_until:
        status = "overdue"
    else:
        status = "due"
    return MemberCycle(status, row.due_date, max(balance, 0))


@dataclass(frozen=True)
class CollectionSummary:
    collection_rate_percent: int | None
    overdue_count: int


def overdue_member_ids(on: date | None = None) -> set[uuid.UUID]:
    on = on or today()
    return set(
        Obligation.objects.filter(
            kind=ObligationKind.CONTRIBUTION, status=ObligationStatus.OPEN, grace_until__lt=on
        ).values_list("member_id", flat=True)
    )


def collection_summary(on: date | None = None) -> CollectionSummary:
    on = on or today()
    cycle = current_cycle(on)
    rate = None
    if cycle is not None:
        totals = Obligation.objects.filter(
            cycle_id=cycle.id, kind=ObligationKind.CONTRIBUTION
        ).aggregate(owed=Sum("amount_minor"), paid=Sum("paid_minor"), waived=Sum("waived_minor"))
        owed = int(totals["owed"] or 0) - int(totals["waived"] or 0)
        if owed > 0:
            rate = min(100, int(totals["paid"] or 0) * 100 // owed)
    return CollectionSummary(rate, len(overdue_member_ids(on)))


@dataclass(frozen=True)
class MemberDue:
    member_id: uuid.UUID
    obligation_id: uuid.UUID
    balance_minor: int


def members_with_balance_due(on: date) -> list[MemberDue]:
    """Contribution obligations due on ``on`` that still have a balance."""
    rows = Obligation.objects.filter(
        kind=ObligationKind.CONTRIBUTION, status=ObligationStatus.OPEN, due_date=on
    ).order_by("member_id")
    return [MemberDue(r.member_id, r.id, r.balance_minor) for r in rows if r.balance_minor > 0]


def obligations_due_on(on: date) -> list[MemberDue]:
    return members_with_balance_due(on)


@dataclass(frozen=True)
class ObligationView:
    obligation_id: uuid.UUID
    kind: str
    due_date: date
    amount_minor: int
    paid_minor: int
    balance_minor: int
    status: str


def obligations_for(member_id: uuid.UUID) -> list[ObligationView]:
    rows = Obligation.objects.filter(member_id=member_id).order_by("-due_date", "-created_at")
    return [
        ObligationView(
            r.id, r.kind, r.due_date, r.amount_minor, r.paid_minor, r.balance_minor, r.status
        )
        for r in rows[:200]
    ]


@dataclass(frozen=True)
class CollectionRow:
    member_id: uuid.UUID
    status: str  # paid | partial | unpaid | overdue
    balance_minor: int


def collection_rows(cycle: Cycle, on: date | None = None) -> list[CollectionRow]:
    on = on or today()
    result = []
    for r in Obligation.objects.filter(cycle_id=cycle.id, kind=ObligationKind.CONTRIBUTION):
        balance = r.balance_minor
        if balance <= 0:
            status = "paid"
        elif on > r.grace_until:
            status = "overdue"
        elif r.paid_minor > 0:
            status = "partial"
        else:
            status = "unpaid"
        result.append(CollectionRow(r.member_id, status, max(balance, 0)))
    return result


def open_fines_total(member_id: uuid.UUID) -> int:
    rows = Obligation.objects.filter(
        Q(member_id=member_id), kind=ObligationKind.FINE, status=ObligationStatus.OPEN
    )
    return sum(r.balance_minor for r in rows)
