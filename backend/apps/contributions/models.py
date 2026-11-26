"""Contribution cycles, obligations, allocations and fine waivers. Internal: other modules use
``apps.contributions.services``.

- A ``Cycle`` is one contribution period of a chama (a week or a month), generated from the
  constitution. Dates are Africa/Nairobi business dates.
- An ``Obligation`` is what one member owes: a contribution for a cycle, or a late fine. Its
  ``paid_minor`` and ``waived_minor`` move as money is allocated or a fine is waived; the money
  itself lives in the ledger. Every change to ``paid_minor`` has an append-only
  ``ObligationPayment`` row, so the running total can always be rebuilt.
- An ``Allocation`` is the record of how one payment (``source_key``) was split; it makes
  ``allocate`` idempotent.
- A ``FineWaiver`` is requested by one official and approved by a different one.

Members are referenced by membership id with composite foreign keys in SQL; no ORM relation
crosses the module boundary.
"""

import uuid
from typing import ClassVar

from django.db import models


class CycleStatus(models.TextChoices):
    OPEN = "open"
    CLOSED = "closed"


class Cycle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    frequency = models.CharField(max_length=16)
    period_start = models.DateField()
    due_date = models.DateField()
    grace_until = models.DateField()  # last day without a fine
    amount_minor = models.BigIntegerField()
    status = models.CharField(max_length=8, choices=CycleStatus.choices, default=CycleStatus.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="contrib_cycle_chama_id_uniq"),
            models.UniqueConstraint(
                fields=["chama", "period_start"], name="contrib_cycle_period_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="contrib_cycle_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(due_date__gte=models.F("period_start"))
                & models.Q(grace_until__gte=models.F("due_date")),
                name="contrib_cycle_dates_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=CycleStatus.values), name="contrib_cycle_status_valid"
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "due_date"], name="contrib_cycle_due_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class ObligationKind(models.TextChoices):
    CONTRIBUTION = "contribution"
    FINE = "fine"


class ObligationStatus(models.TextChoices):
    OPEN = "open"
    PAID = "paid"
    WAIVED = "waived"


class Obligation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    member_id = models.UUIDField()
    kind = models.CharField(max_length=16, choices=ObligationKind.choices)
    cycle_id = models.UUIDField(null=True, blank=True)  # contributions only
    # The contribution a fine is for (fines only): one fine per late contribution.
    fined_obligation_id = models.UUIDField(null=True, blank=True)
    due_date = models.DateField()
    grace_until = models.DateField()
    amount_minor = models.BigIntegerField()
    paid_minor = models.BigIntegerField(default=0)
    waived_minor = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=8, choices=ObligationStatus.choices, default=ObligationStatus.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="contrib_oblig_chama_id_uniq"),
            models.UniqueConstraint(
                fields=["chama", "cycle_id", "member_id"],
                condition=models.Q(kind="contribution"),
                name="contrib_oblig_one_per_cycle",
            ),
            models.UniqueConstraint(
                fields=["chama", "fined_obligation_id"],
                condition=models.Q(kind="fine"),
                name="contrib_oblig_one_fine",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=ObligationKind.values), name="contrib_oblig_kind_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ObligationStatus.values),
                name="contrib_oblig_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0, paid_minor__gte=0, waived_minor__gte=0)
                & models.Q(amount_minor__gte=models.F("paid_minor") + models.F("waived_minor")),
                name="contrib_oblig_amounts_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="contribution", cycle_id__isnull=False)
                    | models.Q(kind="fine", fined_obligation_id__isnull=False)
                ),
                name="contrib_oblig_reference_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(
                fields=["chama", "member_id", "status", "due_date"], name="contrib_oblig_member_idx"
            ),
            models.Index(fields=["chama", "cycle_id"], name="contrib_oblig_cycle_idx"),
            models.Index(fields=["chama", "status", "due_date"], name="contrib_oblig_due_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)

    @property
    def balance_minor(self) -> int:
        return self.amount_minor - self.paid_minor - self.waived_minor


class ObligationPayment(models.Model):
    """Append-only: one row per part of a payment applied to an obligation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    obligation_id = models.UUIDField()
    source_key = models.CharField(max_length=120)
    amount_minor = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["chama", "obligation_id", "source_key"], name="contrib_obpay_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="contrib_obpay_amount_positive"
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)


class Allocation(models.Model):
    """Append-only: how one payment was split (``allocate`` returns this on a repeat call)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    source_key = models.CharField(max_length=120)
    member_id = models.UUIDField()
    debit_account = models.CharField(max_length=32)
    amount_minor = models.BigIntegerField()
    fines_minor = models.BigIntegerField()
    loans_minor = models.BigIntegerField()
    contributions_minor = models.BigIntegerField()
    savings_minor = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "source_key"], name="contrib_alloc_key_uniq"),
            models.CheckConstraint(
                condition=models.Q(
                    amount_minor=models.F("fines_minor")
                    + models.F("loans_minor")
                    + models.F("contributions_minor")
                    + models.F("savings_minor")
                ),
                name="contrib_alloc_sums",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)


class WaiverStatus(models.TextChoices):
    PENDING = "pending"
    APPROVED = "approved"


class FineWaiver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    obligation_id = models.UUIDField()
    member_id = models.UUIDField()  # whose fine
    reason = models.CharField(max_length=200)
    requested_by_member_id = models.UUIDField()
    approved_by_member_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(
        max_length=8, choices=WaiverStatus.choices, default=WaiverStatus.PENDING
    )
    amount_minor = models.BigIntegerField(null=True, blank=True)  # set on approval
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(status__in=WaiverStatus.values),
                name="contrib_waiver_status_valid",
            ),
            # Maker-checker: the approver is never the requester nor the fined member.
            models.CheckConstraint(
                condition=models.Q(approved_by_member_id__isnull=True)
                | (
                    ~models.Q(approved_by_member_id=models.F("requested_by_member_id"))
                    & ~models.Q(approved_by_member_id=models.F("member_id"))
                ),
                name="contrib_waiver_two_people",
            ),
            models.UniqueConstraint(
                fields=["chama", "obligation_id"],
                condition=models.Q(status="pending"),
                name="contrib_waiver_one_pending",
            ),
        ]
        indexes: ClassVar = [
            models.Index(
                fields=["chama", "status", "created_at"], name="contrib_waiver_status_idx"
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)
