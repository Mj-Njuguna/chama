"""Public interface of the ``ledger`` module.

Double-entry accounting: chart of accounts per chama, member sub-accounts, append-only journal,
manual receipts and (later) two-official expenses.

Other modules post business events as balanced transactions and read balances. Corrections are
new compensating transactions, never edits.

Plain values in, plain values out. Every call runs in the caller's chama context (RLS) and
transaction: a posting commits or rolls back with the business change that caused it.

Boundary rules:
- Other modules may import ONLY ``apps.ledger.services`` and ``apps.ledger.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Sum

from apps.ledger.models import (
    CASH_ACCOUNTS,
    PER_MEMBER_ACCOUNTS,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    TransactionKind,
)

__all__ = [
    "LedgerAccount",
    "Line",
    "Posted",
    "TransactionKind",
    "UnbalancedPosting",
    "account_balance",
    "balances_by_member",
    "cash_balance",
    "member_balance",
    "post",
    "posted",
    "reverse",
    "transactions_for_member",
]

_KEY_CONSTRAINT = "ledger_txn_key_uniq"


class UnbalancedPosting(ValueError):
    """Lines that do not sum to zero, fewer than two, or a per-member line without a member
    (a programming error)."""


@dataclass(frozen=True)
class Line:
    """One entry: ``amount_minor`` positive for a debit, negative for a credit."""

    ledger_account: str
    amount_minor: int
    member_id: uuid.UUID | None = None


def post(
    chama_id: uuid.UUID,
    kind: str,
    key: str,
    lines: Sequence[Line],
    *,
    member_id: uuid.UUID | None = None,
    memo: str = "",
) -> bool:
    """Record one balanced transaction for the business event ``key``. Returns False, and
    records nothing, when that event was already posted (idempotent)."""
    kept = [line for line in lines if line.amount_minor != 0]
    if len(kept) < 2 or sum(line.amount_minor for line in kept) != 0:
        raise UnbalancedPosting(f"{key}: lines must balance and be at least two")
    for line in kept:
        if line.ledger_account in PER_MEMBER_ACCOUNTS and line.member_id is None:
            raise UnbalancedPosting(f"{key}: {line.ledger_account} needs a member")
    if LedgerTransaction.objects.filter(key=key).exists():
        return False
    try:
        with transaction.atomic():
            txn = LedgerTransaction.objects.create(
                chama_id=chama_id,
                kind=TransactionKind(kind),
                key=key,
                member_id=member_id,
                memo=memo[:200],
            )
            LedgerEntry.objects.bulk_create(
                LedgerEntry(
                    chama_id=chama_id,
                    transaction_id=txn.id,
                    ledger_account=LedgerAccount(line.ledger_account),
                    amount_minor=line.amount_minor,
                    member_id=line.member_id,
                )
                for line in kept
            )
    except IntegrityError as error:  # the same event posted concurrently: nothing to do
        diag = getattr(getattr(error, "__cause__", None), "diag", None)
        if getattr(diag, "constraint_name", None) == _KEY_CONSTRAINT:
            return False
        raise
    return True


def posted(key: str) -> bool:
    """Whether the business event ``key`` has been posted in the current chama."""
    return bool(LedgerTransaction.objects.filter(key=key).exists())


def reverse(chama_id: uuid.UUID, original_key: str, key: str, memo: str = "") -> bool:
    """Post the exact reverse of the transaction ``original_key`` (a compensating entry).
    Idempotent on ``key``; raises LookupError if the original is missing."""
    txn = LedgerTransaction.objects.filter(key=original_key).first()
    if txn is None:
        raise LookupError(original_key)
    lines = [
        Line(e.ledger_account, -e.amount_minor, e.member_id)
        for e in LedgerEntry.objects.filter(transaction_id=txn.id).order_by("id")
    ]
    return post(chama_id, TransactionKind.REVERSAL, key, lines, member_id=txn.member_id, memo=memo)


def account_balance(ledger_account: str) -> int:
    """The balance of ``ledger_account`` across the current chama, debit positive."""
    total = LedgerEntry.objects.filter(ledger_account=LedgerAccount(ledger_account)).aggregate(
        total=Sum("amount_minor")
    )["total"]
    return int(total or 0)


def cash_balance() -> int:
    """Money the chama holds in its own paybill, till and bank accounts (debit positive)."""
    total = LedgerEntry.objects.filter(ledger_account__in=CASH_ACCOUNTS).aggregate(
        total=Sum("amount_minor")
    )["total"]
    return int(total or 0)


def member_balance(ledger_account: str, member_id: uuid.UUID) -> int:
    """A member's balance on a per-member account, debit positive (savings are credits, so a
    member with savings has a negative ``member_savings`` balance)."""
    total = LedgerEntry.objects.filter(
        ledger_account=LedgerAccount(ledger_account), member_id=member_id
    ).aggregate(total=Sum("amount_minor"))["total"]
    return int(total or 0)


def balances_by_member(
    ledger_account: str, member_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, int]:
    """Per-member balances of ``ledger_account``, debit positive. Members with no entries are
    absent."""
    rows = LedgerEntry.objects.filter(ledger_account=LedgerAccount(ledger_account))
    if member_ids is not None:
        rows = rows.filter(member_id__in=set(member_ids))
    totals = rows.values("member_id").annotate(total=Sum("amount_minor")).order_by()
    return {r["member_id"]: int(r["total"]) for r in totals if r["member_id"] is not None}


@dataclass(frozen=True)
class Posted:
    transaction_id: uuid.UUID
    kind: str
    key: str
    memo: str
    created_at: str
    lines: list[Line]


def transactions_for_member(member_id: uuid.UUID, limit: int = 50) -> list[Posted]:
    """The member's transactions, newest first, with the lines that touch any account."""
    found = LedgerTransaction.objects.filter(
        id__in=LedgerEntry.objects.filter(member_id=member_id).values("transaction_id")
    ).order_by("-created_at", "-id")[:limit]
    result = []
    for txn in found:
        lines = [
            Line(e.ledger_account, e.amount_minor, e.member_id)
            for e in LedgerEntry.objects.filter(transaction_id=txn.id).order_by("id")
        ]
        result.append(
            Posted(txn.id, txn.kind, txn.key, txn.memo, txn.created_at.isoformat(), lines)
        )
    return result
