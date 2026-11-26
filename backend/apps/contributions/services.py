"""Public interface of the ``contributions`` module.

Contribution schedules, cycles and obligations, payment allocation and automated late
fines.

Boundary rules:
- Other modules may import ONLY ``apps.contributions.services`` and ``apps.contributions.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""

# ---------------------------------------------------------------------------------------------
# TEMPORARY STUB (stream P, phase 2). DROP AT MERGE in favour of stream C's implementation.
# It matches the M2 contract signature but skips the allocation order (fines, loan
# installments, contributions): everything goes to member savings.
# ---------------------------------------------------------------------------------------------
import uuid as _uuid
from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class Allocation:
    fines_minor: int
    loans_minor: int
    contributions_minor: int
    savings_minor: int


def allocate(
    chama_id: _uuid.UUID,
    *,
    member_id: _uuid.UUID,
    amount_minor: int,
    source_key: str,
    debit_account: str,
) -> Allocation:
    from apps.ledger.services import LedgerAccount, Line, TransactionKind, post

    post(
        chama_id,
        TransactionKind.ALLOCATION,
        f"alloc:{source_key}",
        [
            Line(debit_account, amount_minor),
            Line(LedgerAccount.MEMBER_SAVINGS, -amount_minor, member_id),
        ],
        member_id=member_id,
        memo=source_key,
    )
    return Allocation(0, 0, 0, amount_minor)
