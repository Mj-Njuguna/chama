"""Public interface of the ``contributions`` module.

Contribution schedules, cycles and obligations, payment allocation and automated late fines.

Boundary rules:
- Other modules may import ONLY ``apps.contributions.services`` and
  ``apps.contributions.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.

Every function runs in the caller's chama context (RLS) and transaction.
"""

import uuid
from datetime import date

from apps.contributions.allocation import AllocationResult
from apps.contributions.allocation import allocate as _allocate
from apps.contributions.fines import pending_waiver_count
from apps.contributions.summaries import (
    CollectionSummary,
    MemberCycle,
    MemberDue,
    collection_summary,
    member_cycle,
    members_with_balance_due,
)

# The contract name for the split of one payment.
Allocation = AllocationResult

__all__ = [
    "Allocation",
    "CollectionSummary",
    "MemberCycle",
    "MemberDue",
    "allocate",
    "collection_summary",
    "cycle_summary_for_member",
    "members_with_balance_due",
    "pending_approvals_count",
]


def allocate(
    chama_id: uuid.UUID,
    *,
    member_id: uuid.UUID,
    amount_minor: int,
    source_key: str,
    debit_account: str,
) -> Allocation:
    """Split one payment for ``member_id``: fines, due loan installments, contributions (each
    oldest first), remainder to savings. ONE ledger transaction keyed ``alloc:{source_key}``.
    ``debit_account`` is ``cash_mpesa``, ``cash_bank`` or ``unallocated``. Idempotent on
    ``source_key`` (returns the first split)."""
    return _allocate(
        chama_id,
        member_id=member_id,
        amount_minor=amount_minor,
        source_key=source_key,
        debit_account=debit_account,
    )


def cycle_summary_for_member(member_id: uuid.UUID, on: date | None = None) -> MemberCycle:
    """The member's status in the current cycle: paid | due | overdue | none."""
    return member_cycle(member_id, on)


def pending_approvals_count() -> int:
    """Approvals waiting for an official (fine waivers; loans and expenses join later)."""
    return pending_waiver_count()
