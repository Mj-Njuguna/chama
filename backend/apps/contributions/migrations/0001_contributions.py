"""Contribution cycles, obligations, allocations and fine waivers. Generated, then edited:

- ``chama_rls`` on every table.
- Composite foreign keys to memberships, cycles and obligations (same chama only).
- Append-only: the app role may only SELECT and INSERT allocations and obligation payments.
  Obligations (running paid/waived totals) and waivers (approval) are updated; never deleted.
"""

import uuid

import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models

from core.tenancy import chama_rls

FOREIGN_KEYS = """
ALTER TABLE contributions_obligation ADD CONSTRAINT contrib_oblig_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_obligation ADD CONSTRAINT contrib_oblig_cycle_fk
    FOREIGN KEY (chama_id, cycle_id) REFERENCES contributions_cycle (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_obligation ADD CONSTRAINT contrib_oblig_fined_fk
    FOREIGN KEY (chama_id, fined_obligation_id) REFERENCES contributions_obligation (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_obligationpayment ADD CONSTRAINT contrib_obpay_oblig_fk
    FOREIGN KEY (chama_id, obligation_id) REFERENCES contributions_obligation (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_allocation ADD CONSTRAINT contrib_alloc_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_finewaiver ADD CONSTRAINT contrib_waiver_oblig_fk
    FOREIGN KEY (chama_id, obligation_id) REFERENCES contributions_obligation (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_finewaiver ADD CONSTRAINT contrib_waiver_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE contributions_finewaiver ADD CONSTRAINT contrib_waiver_requester_fk
    FOREIGN KEY (chama_id, requested_by_member_id)
    REFERENCES organizations_membership (chama_id, id) ON DELETE RESTRICT;
ALTER TABLE contributions_finewaiver ADD CONSTRAINT contrib_waiver_approver_fk
    FOREIGN KEY (chama_id, approved_by_member_id)
    REFERENCES organizations_membership (chama_id, id) ON DELETE RESTRICT;
"""
DROP_FOREIGN_KEYS = """
ALTER TABLE contributions_finewaiver DROP CONSTRAINT contrib_waiver_approver_fk;
ALTER TABLE contributions_finewaiver DROP CONSTRAINT contrib_waiver_requester_fk;
ALTER TABLE contributions_finewaiver DROP CONSTRAINT contrib_waiver_member_fk;
ALTER TABLE contributions_finewaiver DROP CONSTRAINT contrib_waiver_oblig_fk;
ALTER TABLE contributions_allocation DROP CONSTRAINT contrib_alloc_member_fk;
ALTER TABLE contributions_obligationpayment DROP CONSTRAINT contrib_obpay_oblig_fk;
ALTER TABLE contributions_obligation DROP CONSTRAINT contrib_oblig_fined_fk;
ALTER TABLE contributions_obligation DROP CONSTRAINT contrib_oblig_cycle_fk;
ALTER TABLE contributions_obligation DROP CONSTRAINT contrib_oblig_member_fk;
"""

GRANTS = """
REVOKE UPDATE, DELETE ON contributions_allocation, contributions_obligationpayment FROM app_rw;
REVOKE DELETE ON contributions_cycle, contributions_obligation, contributions_finewaiver
    FROM app_rw;
"""
REVERSE_GRANTS = """
GRANT UPDATE, DELETE ON contributions_allocation, contributions_obligationpayment TO app_rw;
GRANT DELETE ON contributions_cycle, contributions_obligation, contributions_finewaiver TO app_rw;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organizations", "0002_security"),
    ]

    operations = [
        migrations.CreateModel(
            name="Allocation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("source_key", models.CharField(max_length=120)),
                ("member_id", models.UUIDField()),
                ("debit_account", models.CharField(max_length=32)),
                ("amount_minor", models.BigIntegerField()),
                ("fines_minor", models.BigIntegerField()),
                ("loans_minor", models.BigIntegerField()),
                ("contributions_minor", models.BigIntegerField()),
                ("savings_minor", models.BigIntegerField()),
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
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "source_key"), name="contrib_alloc_key_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "amount_minor",
                                django.db.models.expressions.CombinedExpression(
                                    django.db.models.expressions.CombinedExpression(
                                        django.db.models.expressions.CombinedExpression(
                                            models.F("fines_minor"), "+", models.F("loans_minor")
                                        ),
                                        "+",
                                        models.F("contributions_minor"),
                                    ),
                                    "+",
                                    models.F("savings_minor"),
                                ),
                            )
                        ),
                        name="contrib_alloc_sums",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Cycle",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("frequency", models.CharField(max_length=16)),
                ("period_start", models.DateField()),
                ("due_date", models.DateField()),
                ("grace_until", models.DateField()),
                ("amount_minor", models.BigIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("closed", "Closed")],
                        default="open",
                        max_length=8,
                    ),
                ),
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
                    models.Index(fields=["chama", "due_date"], name="contrib_cycle_due_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "id"), name="contrib_cycle_chama_id_uniq"
                    ),
                    models.UniqueConstraint(
                        fields=("chama", "period_start"), name="contrib_cycle_period_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("amount_minor__gt", 0)),
                        name="contrib_cycle_amount_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("due_date__gte", models.F("period_start")),
                            ("grace_until__gte", models.F("due_date")),
                        ),
                        name="contrib_cycle_dates_ordered",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("status__in", ["open", "closed"])),
                        name="contrib_cycle_status_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="FineWaiver",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("obligation_id", models.UUIDField()),
                ("member_id", models.UUIDField()),
                ("reason", models.CharField(max_length=200)),
                ("requested_by_member_id", models.UUIDField()),
                ("approved_by_member_id", models.UUIDField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("approved", "Approved")],
                        default="pending",
                        max_length=8,
                    ),
                ),
                ("amount_minor", models.BigIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
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
                        fields=["chama", "status", "created_at"], name="contrib_waiver_status_idx"
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("status__in", ["pending", "approved"])),
                        name="contrib_waiver_status_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("approved_by_member_id__isnull", True),
                            models.Q(
                                models.Q(
                                    ("approved_by_member_id", models.F("requested_by_member_id")),
                                    _negated=True,
                                ),
                                models.Q(
                                    ("approved_by_member_id", models.F("member_id")), _negated=True
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="contrib_waiver_two_people",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("status", "pending")),
                        fields=("chama", "obligation_id"),
                        name="contrib_waiver_one_pending",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Obligation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("member_id", models.UUIDField()),
                (
                    "kind",
                    models.CharField(
                        choices=[("contribution", "Contribution"), ("fine", "Fine")], max_length=16
                    ),
                ),
                ("cycle_id", models.UUIDField(blank=True, null=True)),
                ("fined_obligation_id", models.UUIDField(blank=True, null=True)),
                ("due_date", models.DateField()),
                ("grace_until", models.DateField()),
                ("amount_minor", models.BigIntegerField()),
                ("paid_minor", models.BigIntegerField(default=0)),
                ("waived_minor", models.BigIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("paid", "Paid"), ("waived", "Waived")],
                        default="open",
                        max_length=8,
                    ),
                ),
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
                        fields=["chama", "member_id", "status", "due_date"],
                        name="contrib_oblig_member_idx",
                    ),
                    models.Index(fields=["chama", "cycle_id"], name="contrib_oblig_cycle_idx"),
                    models.Index(
                        fields=["chama", "status", "due_date"], name="contrib_oblig_due_idx"
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "id"), name="contrib_oblig_chama_id_uniq"
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("kind", "contribution")),
                        fields=("chama", "cycle_id", "member_id"),
                        name="contrib_oblig_one_per_cycle",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("kind", "fine")),
                        fields=("chama", "fined_obligation_id"),
                        name="contrib_oblig_one_fine",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("kind__in", ["contribution", "fine"])),
                        name="contrib_oblig_kind_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("status__in", ["open", "paid", "waived"])),
                        name="contrib_oblig_status_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("amount_minor__gt", 0),
                            ("paid_minor__gte", 0),
                            ("waived_minor__gte", 0),
                            (
                                "amount_minor__gte",
                                django.db.models.expressions.CombinedExpression(
                                    models.F("paid_minor"), "+", models.F("waived_minor")
                                ),
                            ),
                        ),
                        name="contrib_oblig_amounts_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("cycle_id__isnull", False), ("kind", "contribution")),
                            models.Q(("fined_obligation_id__isnull", False), ("kind", "fine")),
                            _connector="OR",
                        ),
                        name="contrib_oblig_reference_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ObligationPayment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("obligation_id", models.UUIDField()),
                ("source_key", models.CharField(max_length=120)),
                ("amount_minor", models.BigIntegerField()),
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
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "obligation_id", "source_key"), name="contrib_obpay_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("amount_minor__gt", 0)),
                        name="contrib_obpay_amount_positive",
                    ),
                ],
            },
        ),
        chama_rls("contributions_cycle"),
        chama_rls("contributions_obligation"),
        chama_rls("contributions_obligationpayment"),
        chama_rls("contributions_allocation"),
        chama_rls("contributions_finewaiver"),
        migrations.RunSQL(FOREIGN_KEYS, DROP_FOREIGN_KEYS),
        migrations.RunSQL(GRANTS, REVERSE_GRANTS),
    ]
