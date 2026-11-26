"""Payment allocation (internal; exposed as ``contributions.services.allocate``).

One payment for one member is split, in this order:

1. open fines, oldest first;
2. due loan installments, oldest first (interest, then principal), from ``lending``;
3. open contribution obligations, oldest first;
4. the remainder becomes savings (advance savings).

It posts ONE balanced ledger transaction keyed ``alloc:{source_key}`` (Dr the money's account,
Cr the member's sub-accounts), updates the obligations' paid totals with append-only payment
rows, and records the split. A repeat call with the same ``source_key`` changes nothing and
returns the first split. Runs in the caller's transaction and chama context.
"""

import uuid
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F

from apps.contributions.models import (
    Allocation,
    Obligation,
    ObligationKind,
    ObligationPayment,
    ObligationStatus,
)
from apps.ledger.services import LedgerAccount, Line, TransactionKind, post

DEBIT_ACCOUNTS = frozenset(
    {LedgerAccount.CASH_MPESA, LedgerAccount.CASH_BANK, LedgerAccount.UNALLOCATED}
)


@dataclass(frozen=True)
class AllocationResult:
    fines_minor: int
    loans_minor: int
    contributions_minor: int
    savings_minor: int


def _result(row: Allocation) -> AllocationResult:
    return AllocationResult(
        row.fines_minor, row.loans_minor, row.contributions_minor, row.savings_minor
    )


def _apply(obligation: Obligation, amount: int, source_key: str) -> None:
    ObligationPayment.objects.create(
        chama_id=obligation.chama_id,
        obligation_id=obligation.id,
        source_key=source_key,
        amount_minor=amount,
    )
    paid_off = amount == obligation.balance_minor
    Obligation.objects.filter(id=obligation.id).update(
        paid_minor=F("paid_minor") + amount,
        status=ObligationStatus.PAID if paid_off else ObligationStatus.OPEN,
    )


def allocate(
    chama_id: uuid.UUID,
    *,
    member_id: uuid.UUID,
    amount_minor: int,
    source_key: str,
    debit_account: str,
) -> AllocationResult:
    if amount_minor <= 0:
        raise ValueError("allocate needs a positive amount")
    if debit_account not in DEBIT_ACCOUNTS:
        raise ValueError(f"cannot allocate from {debit_account}")
    with transaction.atomic():
        done = Allocation.objects.filter(source_key=source_key).first()
        if done is not None:
            return _result(done)

        remaining = amount_minor
        credits: list[Line] = []
        open_rows = list(
            Obligation.objects.select_for_update()
            .filter(member_id=member_id, status=ObligationStatus.OPEN)
            .order_by("due_date", "created_at", "id")
        )

        fines = 0
        for row in (r for r in open_rows if r.kind == ObligationKind.FINE):
            pay = min(remaining, row.balance_minor)
            if pay <= 0:
                break
            _apply(row, pay, source_key)
            fines += pay
            remaining -= pay
        if fines:
            credits.append(Line(LedgerAccount.FINES_RECEIVABLE, -fines, member_id))

        loans = _pay_installments(member_id, remaining, source_key, credits)
        remaining -= loans

        contributions = 0
        for row in (r for r in open_rows if r.kind == ObligationKind.CONTRIBUTION):
            pay = min(remaining, row.balance_minor)
            if pay <= 0:
                break
            _apply(row, pay, source_key)
            contributions += pay
            remaining -= pay

        savings = remaining
        if contributions + savings:
            credits.append(
                Line(LedgerAccount.MEMBER_SAVINGS, -(contributions + savings), member_id)
            )
        post(
            chama_id,
            TransactionKind.ALLOCATION,
            f"alloc:{source_key}",
            [Line(debit_account, amount_minor), *credits],
            member_id=member_id,
            memo=source_key.split(":", 1)[0],
        )
        record = Allocation.objects.create(
            chama_id=chama_id,
            source_key=source_key,
            member_id=member_id,
            debit_account=debit_account,
            amount_minor=amount_minor,
            fines_minor=fines,
            loans_minor=loans,
            contributions_minor=contributions,
            savings_minor=savings,
        )
        return _result(record)


def _pay_installments(
    member_id: uuid.UUID, available: int, source_key: str, credits: list[Line]
) -> int:
    """Pay due loan installments (interest first, then principal), oldest first."""
    from apps.lending.services import due_installments, record_installment_payment

    paid_interest = paid_principal = 0
    for item in due_installments(member_id):
        if available <= 0:
            break
        interest = min(available, item.interest_due_minor)
        available -= interest
        principal = min(available, item.principal_due_minor)
        available -= principal
        if interest or principal:
            record_installment_payment(
                item.installment_id,
                interest_minor=interest,
                principal_minor=principal,
                source_key=source_key,
            )
        paid_interest += interest
        paid_principal += principal
    if paid_interest:
        credits.append(Line(LedgerAccount.INTEREST_RECEIVABLE, -paid_interest, member_id))
    if paid_principal:
        credits.append(Line(LedgerAccount.LOANS_RECEIVABLE, -paid_principal, member_id))
    return paid_interest + paid_principal
