"""Organizations models (internal; other modules use ``apps.organizations.services``)."""

import uuid
from decimal import Decimal
from typing import ClassVar

from django.db import models

from apps.organizations.codes import new_member_code
from apps.organizations.roles import OFFICIALS, Role


class Chama(models.Model):
    """The SaaS customer (one chama) and the tenant root for row-level security.

    UUID v4 primary key, because the ID is exposed through the API and must not reveal how many
    chamas exist. Payment credentials never live here; they get their own encrypted table.
    """

    class Status(models.TextChoices):
        ACTIVE = "active"
        SUSPENDED = "suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    # "This wasn't me": set by any official, cleared only by an operator after manual review
    # (``unfreeze_chama``). While set, every step-up action is refused.
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by_user_id = models.UUIDField(null=True, blank=True)

    def __str__(self) -> str:
        return str(self.id)


class MembershipStatus(models.TextChoices):
    ACTIVE = "active"
    REVOKED = "revoked"
    INVITED = "invited"  # waiting for the invited person to accept; grants nothing


class Membership(models.Model):
    """A user's place in one chama, with one role and the member reference code they pay with.

    Under chama RLS like any chama table, plus the read-only ``user_self_read`` policy so a
    verified user can list their own memberships before a chama is active. Memberships are
    revoked, never deleted, so history stays intact.

    ``member_code`` is what the member types in the M-Pesa account field; it is unique within
    the chama and never changes. ``roster_name`` is the name the officials entered (invite or
    roster import); the person's own display name lives on the user.
    """

    Status = MembershipStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=16, choices=Role.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    member_code = models.CharField(max_length=12, default=new_member_code)
    roster_name = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    # Accepting the invitation accepts the invite terms (wording version recorded). The
    # due-date STK prompt is opt-in per member and on by default for members who accept.
    terms_version = models.CharField(max_length=16, blank=True, default="")
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    scheduled_stk_opt_in = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values), name="organizations_membership_role_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=MembershipStatus.values),
                name="organizations_membership_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(member_code__regex=r"^[2-9A-HJ-NP-Z]{6}$"),
                name="organizations_membership_code_format",
            ),
            models.UniqueConstraint(fields=["chama", "user"], name="organizations_membership_uniq"),
            models.UniqueConstraint(
                fields=["chama", "member_code"], name="organizations_membership_code_uniq"
            ),
            # Target of composite foreign keys from other chama tables.
            models.UniqueConstraint(
                fields=["chama", "id"], name="organizations_membership_chama_id_id_uniq"
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)

    @property
    def is_official(self) -> bool:
        return self.role in OFFICIALS


class ContributionFrequency(models.TextChoices):
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class PenaltyType(models.TextChoices):
    NONE = "none"
    FLAT = "flat"
    PERCENT = "percent"


class InterestMethod(models.TextChoices):
    REDUCING = "reducing"
    FLAT = "flat"


class PaymentChannel(models.TextChoices):
    PAYBILL = "paybill"  # recommended: carries the member code in the account field
    TILL = "till"  # no account reference: every payment needs an official to allocate it


class Constitution(models.Model):
    """The chama's rules: contributions, fines and loans. One row per chama, created with it.

    Money in KES minor units. Rates in basis points (1% = 100). ``due_day``: day of the month
    (1-28) for monthly contributions, weekday (0 = Monday ... 6 = Sunday) for weekly ones.
    Edited by officials with a fresh server PIN; every change bumps ``version`` and is audited.
    """

    chama = models.OneToOneField(
        Chama, on_delete=models.PROTECT, primary_key=True, related_name="+"
    )
    contribution_frequency = models.CharField(
        max_length=16, choices=ContributionFrequency.choices, default=ContributionFrequency.MONTHLY
    )
    contribution_amount_minor = models.PositiveBigIntegerField(default=0)
    due_day = models.PositiveSmallIntegerField(default=1)
    grace_days = models.PositiveSmallIntegerField(default=3)
    penalty_type = models.CharField(
        max_length=16, choices=PenaltyType.choices, default=PenaltyType.NONE
    )
    penalty_flat_minor = models.PositiveBigIntegerField(default=0)
    penalty_rate_bp = models.PositiveIntegerField(default=0)
    loan_multiplier = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("3.0"))
    interest_method = models.CharField(
        max_length=16, choices=InterestMethod.choices, default=InterestMethod.REDUCING
    )
    interest_rate_bp = models.PositiveIntegerField(default=0)  # per month
    max_loan_months = models.PositiveSmallIntegerField(default=12)
    payment_channel = models.CharField(
        max_length=16, choices=PaymentChannel.choices, default=PaymentChannel.PAYBILL
    )
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(contribution_frequency__in=ContributionFrequency.values),
                name="organizations_constitution_frequency_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(penalty_type__in=PenaltyType.values),
                name="organizations_constitution_penalty_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(interest_method__in=InterestMethod.values),
                name="organizations_constitution_interest_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(payment_channel__in=PaymentChannel.values),
                name="organizations_constitution_channel_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(contribution_frequency="monthly", due_day__gte=1, due_day__lte=28)
                    | models.Q(contribution_frequency="weekly", due_day__lte=6)
                ),
                name="organizations_constitution_due_day_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(grace_days__lte=30),
                name="organizations_constitution_grace_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(penalty_rate_bp__lte=10000, interest_rate_bp__lte=10000),
                name="organizations_constitution_rates_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(loan_multiplier__gte=0, loan_multiplier__lte=20),
                name="organizations_constitution_multiplier_valid",
            ),
        ]

    def __str__(self) -> str:
        return str(self.chama_id)


class PendingChangeKind(models.TextChoices):
    """Chama changes that wait out a cooling-off period."""

    PAYMENT_DETAILS = "payment_details"  # the chama's paybill/till and Daraja credentials


class PendingChangeStatus(models.TextChoices):
    PENDING = "pending"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PendingChange(models.Model):
    """A requested high-risk chama change, applied only after its cooling-off.

    Requested with a fresh OTP; every official is alerted; any official may cancel it; it is
    applied with a fresh OTP between ``effective_at`` and ``expires_at``, re-checking the freeze
    in the same transaction. Under chama RLS. Rows are closed, never deleted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=32, choices=PendingChangeKind.choices)
    status = models.CharField(
        max_length=16, choices=PendingChangeStatus.choices, default=PendingChangeStatus.PENDING
    )
    requested_by_user_id = models.UUIDField()
    requested_device_id = models.UUIDField()
    requested_at = models.DateTimeField()
    effective_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(kind__in=PendingChangeKind.values),
                name="organizations_pendingchange_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=PendingChangeStatus.values),
                name="organizations_pendingchange_status_valid",
            ),
            # One open change of each kind per chama.
            models.UniqueConstraint(
                fields=["chama", "kind"],
                condition=models.Q(status="pending"),
                name="organizations_pendingchange_one_open_per_kind",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)
