"""Payments. Generated, then edited:

- ``chama_rls`` on every chama table; ``payments_callbackroute`` is RLS-exempt (see
  core.tenancy) and holds token hashes only.
- Composite foreign keys to memberships, payments and STK requests of the same chama.
- Payments and their allocations are append-only (except re-encrypting ``payer_name`` on key
  rotation, a column grant); callback routes are written once; status
  rows (linking, STK requests, callback events) are never deleted.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models

import core.crypto
from core.tenancy import chama_rls

FOREIGN_KEYS = """
ALTER TABLE payments_paymentallocation ADD CONSTRAINT payments_alloc_payment_fk
    FOREIGN KEY (chama_id, payment_id) REFERENCES payments_payment (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE payments_paymentallocation ADD CONSTRAINT payments_alloc_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE payments_paymentallocation ADD CONSTRAINT payments_alloc_by_fk
    FOREIGN KEY (chama_id, allocated_by_member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE payments_stkrequest ADD CONSTRAINT payments_stk_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE payments_stkrequest ADD CONSTRAINT payments_stk_payment_fk
    FOREIGN KEY (chama_id, payment_id) REFERENCES payments_payment (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE payments_payment ADD CONSTRAINT payments_payment_stk_fk
    FOREIGN KEY (chama_id, stk_request_id) REFERENCES payments_stkrequest (chama_id, id)
    ON DELETE RESTRICT;
"""
DROP_FOREIGN_KEYS = """
ALTER TABLE payments_payment DROP CONSTRAINT payments_payment_stk_fk;
ALTER TABLE payments_stkrequest DROP CONSTRAINT payments_stk_payment_fk;
ALTER TABLE payments_stkrequest DROP CONSTRAINT payments_stk_member_fk;
ALTER TABLE payments_paymentallocation DROP CONSTRAINT payments_alloc_by_fk;
ALTER TABLE payments_paymentallocation DROP CONSTRAINT payments_alloc_member_fk;
ALTER TABLE payments_paymentallocation DROP CONSTRAINT payments_alloc_payment_fk;
"""

GRANTS = """
REVOKE UPDATE, DELETE ON payments_payment, payments_paymentallocation, payments_callbackroute
    FROM app_rw;
REVOKE DELETE ON payments_paymentlinking, payments_stkrequest, payments_callbackevent FROM app_rw;
-- Key rotation (reencrypt_fields) rewrites the one encrypted column; nothing else changes.
GRANT UPDATE (payer_name) ON payments_payment TO app_rw;
"""
REVERSE_GRANTS = """
REVOKE UPDATE (payer_name) ON payments_payment FROM app_rw;
GRANT UPDATE, DELETE ON payments_payment, payments_paymentallocation, payments_callbackroute
    TO app_rw;
GRANT DELETE ON payments_paymentlinking, payments_stkrequest, payments_callbackevent TO app_rw;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organizations", "0002_security"),
    ]

    operations = [
        migrations.CreateModel(
            name="CallbackRoute",
            fields=[
                ("token_hash", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chama",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CallbackEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("c2b_validation", "C2B Validation"),
                            ("c2b_confirmation", "C2B Confirmation"),
                            ("stk_result", "Stk Result"),
                        ],
                        max_length=24,
                    ),
                ),
                ("body", models.JSONField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("outcome", models.CharField(blank=True, default="", max_length=40)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["chama", "received_at"], name="payments_cbevent_received_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "id"), name="payments_cbevent_chama_id_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("kind__in", ["c2b_validation", "c2b_confirmation", "stk_result"])
                        ),
                        name="payments_cbevent_kind_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Payment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "source",
                    models.CharField(choices=[("c2b", "C2B"), ("stk", "Stk")], max_length=8),
                ),
                ("mpesa_receipt", models.CharField(max_length=32)),
                ("amount_minor", models.BigIntegerField()),
                ("received_at", models.DateTimeField()),
                ("bill_ref", models.CharField(blank=True, default="", max_length=40)),
                ("payer_masked", models.CharField(blank=True, default="", max_length=40)),
                ("payer_name", core.crypto.EncryptedTextField(blank=True, default="")),
                ("stk_request_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["chama", "received_at"], name="payments_payment_received_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "id"), name="payments_payment_chama_id_uniq"
                    ),
                    models.UniqueConstraint(
                        fields=("chama", "mpesa_receipt"), name="payments_payment_receipt_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("amount_minor__gt", 0)),
                        name="payments_payment_amount_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("source__in", ["c2b", "stk"])),
                        name="payments_payment_source_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PaymentAllocation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("payment_id", models.UUIDField()),
                ("member_id", models.UUIDField()),
                ("allocated_by_member_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["chama", "member_id"], name="payments_alloc_member_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "payment_id"), name="payments_alloc_payment_uniq"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PaymentLinking",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("pending", "Pending"),
                            ("replaced", "Replaced"),
                            ("discarded", "Discarded"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[("paybill", "Paybill"), ("till", "Till")], max_length=8
                    ),
                ),
                ("shortcode", models.CharField(max_length=12)),
                (
                    "environment",
                    models.CharField(
                        choices=[("sandbox", "Sandbox"), ("production", "Production")],
                        max_length=12,
                    ),
                ),
                ("consumer_key", core.crypto.EncryptedTextField()),
                ("consumer_secret", core.crypto.EncryptedTextField()),
                ("passkey", core.crypto.EncryptedTextField()),
                ("callback_token", core.crypto.EncryptedTextField()),
                ("change_id", models.UUIDField(blank=True, null=True)),
                ("requested_by_user_id", models.UUIDField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "id"), name="payments_linking_chama_id_uniq"
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("status", "active")),
                        fields=("chama",),
                        name="payments_linking_one_active",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("status", "pending")),
                        fields=("chama",),
                        name="payments_linking_one_pending",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("status__in", ["active", "pending", "replaced", "discarded"])
                        ),
                        name="payments_linking_status_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("channel__in", ["paybill", "till"])),
                        name="payments_linking_channel_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("environment__in", ["sandbox", "production"])),
                        name="payments_linking_environment_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("shortcode__regex", "^[0-9]{5,12}$")),
                        name="payments_linking_shortcode_digits",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StkRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("member_id", models.UUIDField()),
                ("amount_minor", models.BigIntegerField()),
                (
                    "reason",
                    models.CharField(
                        choices=[("member", "Member"), ("scheduled", "Scheduled")], max_length=12
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                            ("timeout", "Timeout"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("checkout_request_id", models.CharField(blank=True, max_length=80, null=True)),
                ("merchant_request_id", models.CharField(blank=True, default="", max_length=80)),
                ("result_code", models.CharField(blank=True, default="", max_length=16)),
                ("payment_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["chama", "status", "created_at"], name="payments_stk_status_idx"
                    ),
                    models.Index(
                        fields=["chama", "member_id", "created_at"], name="payments_stk_member_idx"
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "id"), name="payments_stk_chama_id_uniq"
                    ),
                    models.UniqueConstraint(
                        fields=("chama", "idempotency_key"), name="payments_stk_idempotency_uniq"
                    ),
                    models.UniqueConstraint(
                        fields=("chama", "checkout_request_id"), name="payments_stk_checkout_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("amount_minor__gt", 0)),
                        name="payments_stk_amount_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                ["pending", "succeeded", "failed", "cancelled", "timeout"],
                            )
                        ),
                        name="payments_stk_status_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("reason__in", ["member", "scheduled"])),
                        name="payments_stk_reason_valid",
                    ),
                ],
            },
        ),
        chama_rls("payments_paymentlinking"),
        chama_rls("payments_callbackevent"),
        chama_rls("payments_stkrequest"),
        chama_rls("payments_payment"),
        chama_rls("payments_paymentallocation"),
        migrations.RunSQL(FOREIGN_KEYS, DROP_FOREIGN_KEYS),
        migrations.RunSQL(GRANTS, REVERSE_GRANTS),
    ]
