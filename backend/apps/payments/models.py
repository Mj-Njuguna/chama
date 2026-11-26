"""Payments: the chama's own M-Pesa paybill/till linking, raw callbacks, STK requests, payments
and their allocation to members. Internal: other modules use ``apps.payments.services``.

Non-custodial: money arrives in the chama's OWN shortcode. The platform only records what M-Pesa
reports and never moves funds.
"""

import uuid
from typing import ClassVar

from django.db import models

from core.crypto import EncryptedTextField


class LinkingChannel(models.TextChoices):
    PAYBILL = "paybill"
    TILL = "till"


class LinkingEnvironment(models.TextChoices):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class LinkingStatus(models.TextChoices):
    ACTIVE = "active"  # the details payments use now
    PENDING = "pending"  # waiting out the cooling-off of a replacement
    REPLACED = "replaced"  # an earlier active row
    DISCARDED = "discarded"  # a pending row whose change was cancelled or expired


class PaymentLinking(models.Model):
    """The chama's shortcode and Daraja app credentials. At most one ACTIVE and one PENDING row
    per chama. Secrets are encrypted at rest and never returned by the API."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=16, choices=LinkingStatus.choices)
    channel = models.CharField(max_length=8, choices=LinkingChannel.choices)
    shortcode = models.CharField(max_length=12)
    environment = models.CharField(max_length=12, choices=LinkingEnvironment.choices)
    consumer_key = EncryptedTextField()
    consumer_secret = EncryptedTextField()
    passkey = EncryptedTextField()
    # The secret part of this chama's callback URLs (the same for every linking row).
    callback_token = EncryptedTextField()
    change_id = models.UUIDField(null=True, blank=True)  # the cooling-off change of a PENDING row
    requested_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="payments_linking_chama_id_uniq"),
            models.UniqueConstraint(
                fields=["chama"],
                condition=models.Q(status="active"),
                name="payments_linking_one_active",
            ),
            models.UniqueConstraint(
                fields=["chama"],
                condition=models.Q(status="pending"),
                name="payments_linking_one_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=LinkingStatus.values),
                name="payments_linking_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=LinkingChannel.values),
                name="payments_linking_channel_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(environment__in=LinkingEnvironment.values),
                name="payments_linking_environment_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(shortcode__regex=r"^[0-9]{5,12}$"),
                name="payments_linking_shortcode_digits",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)


class CallbackRoute(models.Model):
    """Maps the SHA-256 hash of a callback URL token to its chama. Read before any chama
    context exists (a provider callback carries no session), so it is RLS-exempt; it holds
    hashes only. Insert-only."""

    token_hash = models.CharField(max_length=64, primary_key=True)
    chama = models.OneToOneField("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.token_hash[:8]


class CallbackKind(models.TextChoices):
    C2B_VALIDATION = "c2b_validation"
    C2B_CONFIRMATION = "c2b_confirmation"
    STK_RESULT = "stk_result"


class CallbackEvent(models.Model):
    """Every provider callback, stored raw before any processing."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=24, choices=CallbackKind.choices)
    body = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="payments_cbevent_chama_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(kind__in=CallbackKind.values),
                name="payments_cbevent_kind_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "received_at"], name="payments_cbevent_received_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class StkStatus(models.TextChoices):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class StkReason(models.TextChoices):
    MEMBER = "member"  # the member tapped "Pay via M-Pesa"
    SCHEDULED = "scheduled"  # the due-date prompt (opted-in members)


class StkRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    member_id = models.UUIDField()
    amount_minor = models.BigIntegerField()
    reason = models.CharField(max_length=12, choices=StkReason.choices)
    idempotency_key = models.CharField(max_length=120)
    status = models.CharField(max_length=12, choices=StkStatus.choices, default=StkStatus.PENDING)
    # NULL until the provider accepts the request; unique per chama once set (NULLs never clash).
    checkout_request_id = models.CharField(max_length=80, null=True, blank=True)  # noqa: DJ001
    merchant_request_id = models.CharField(max_length=80, blank=True, default="")
    result_code = models.CharField(max_length=16, blank=True, default="")
    payment_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="payments_stk_chama_id_uniq"),
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"], name="payments_stk_idempotency_uniq"
            ),
            models.UniqueConstraint(
                fields=["chama", "checkout_request_id"], name="payments_stk_checkout_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="payments_stk_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=StkStatus.values), name="payments_stk_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(reason__in=StkReason.values), name="payments_stk_reason_valid"
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "status", "created_at"], name="payments_stk_status_idx"),
            models.Index(
                fields=["chama", "member_id", "created_at"], name="payments_stk_member_idx"
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)


class PaymentSource(models.TextChoices):
    C2B = "c2b"  # paid to the paybill or till from the M-Pesa menu or app
    STK = "stk"  # paid through an STK prompt


class Payment(models.Model):
    """A confirmed M-Pesa payment into the chama's own shortcode. Append-only; one row per
    M-Pesa receipt (TransID) per chama, so a repeated callback records nothing."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=8, choices=PaymentSource.choices)
    mpesa_receipt = models.CharField(max_length=32)
    amount_minor = models.BigIntegerField()
    received_at = models.DateTimeField()
    bill_ref = models.CharField(max_length=40, blank=True, default="")  # as typed by the payer
    payer_masked = models.CharField(max_length=40, blank=True, default="")  # as M-Pesa sends it
    payer_name = EncryptedTextField(blank=True, default="")
    stk_request_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="payments_payment_chama_id_uniq"),
            models.UniqueConstraint(
                fields=["chama", "mpesa_receipt"], name="payments_payment_receipt_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="payments_payment_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=PaymentSource.values),
                name="payments_payment_source_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "received_at"], name="payments_payment_received_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class PaymentAllocation(models.Model):
    """Which member a payment belongs to. One per payment, append-only. ``allocated_by`` is
    null when the platform matched it (member code or STK request)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    payment_id = models.UUIDField()
    member_id = models.UUIDField()
    allocated_by_member_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["chama", "payment_id"], name="payments_alloc_payment_uniq"
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "member_id"], name="payments_alloc_member_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)
