"""Public interface of the ``lending`` module.

Table-banking loans: eligibility, guarantor pledges, the approval state machine,
amortization schedules and repayments.

Boundary rules:
- Other modules may import ONLY ``apps.lending.services`` and ``apps.lending.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""

import uuid
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DueInstallment:
    """One loan installment that is due (or overdue) for a member, oldest first."""

    installment_id: uuid.UUID
    loan_id: uuid.UUID
    due_date: date
    interest_due_minor: int
    principal_due_minor: int


def due_installments(member_id: uuid.UUID) -> list[DueInstallment]:
    """The member's due loan installments in the current chama, oldest first.

    Phase 3 (table banking) fills this in; until loans exist nobody owes installments.
    """
    return []


def record_installment_payment(
    installment_id: uuid.UUID, *, interest_minor: int, principal_minor: int, source_key: str
) -> None:
    """Record that a payment covered part of an installment (idempotent on ``source_key``).

    Called by ``contributions.services.allocate`` in the same transaction as its ledger posting.
    Phase 3 fills this in; with no installments it is never called.
    """
    raise NotImplementedError("loans arrive in phase 3")
