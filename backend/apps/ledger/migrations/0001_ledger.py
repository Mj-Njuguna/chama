"""The double-entry ledger. Generated, then edited:

- ``chama_rls`` on every table.
- Composite foreign keys: entry -> transaction; transaction, entry and receipt -> membership.
  NULL references are allowed (a foreign key with a NULL column is not checked).
- **Every transaction balances**: a constraint trigger, deferred to commit, on entry and
  transaction insert: the entries of the transaction sum to zero and there are at least two.
  It reads in the checked row's own chama, because the chama setting is already reset at
  commit.
- **Append-only**: the app role may only SELECT and INSERT on all three tables.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models

from core.tenancy import chama_rls, in_row_chama_sql

FOREIGN_KEYS = """
ALTER TABLE ledger_ledgerentry ADD CONSTRAINT ledger_entry_txn_fk
    FOREIGN KEY (chama_id, transaction_id) REFERENCES ledger_ledgertransaction (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE ledger_ledgerentry ADD CONSTRAINT ledger_entry_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE ledger_ledgertransaction ADD CONSTRAINT ledger_txn_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE ledger_receipt ADD CONSTRAINT ledger_receipt_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
ALTER TABLE ledger_receipt ADD CONSTRAINT ledger_receipt_recorded_by_fk
    FOREIGN KEY (chama_id, recorded_by_member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
"""
DROP_FOREIGN_KEYS = """
ALTER TABLE ledger_receipt DROP CONSTRAINT ledger_receipt_recorded_by_fk;
ALTER TABLE ledger_receipt DROP CONSTRAINT ledger_receipt_member_fk;
ALTER TABLE ledger_ledgertransaction DROP CONSTRAINT ledger_txn_member_fk;
ALTER TABLE ledger_ledgerentry DROP CONSTRAINT ledger_entry_member_fk;
ALTER TABLE ledger_ledgerentry DROP CONSTRAINT ledger_entry_txn_fk;
"""

_BALANCE_BODY = in_row_chama_sql(
    "NEW.chama_id",
    """    SELECT COALESCE(sum(e.amount_minor), 0), count(*) INTO total, entries
        FROM ledger_ledgerentry AS e WHERE e.transaction_id = target;""",
)

BALANCED = (
    """
CREATE FUNCTION ledger_transaction_balances() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
    total bigint;
    entries bigint;
    previous_chama text;
BEGIN
    IF TG_TABLE_NAME = 'ledger_ledgertransaction' THEN
        target := NEW.id;
    ELSE
        target := NEW.transaction_id;
    END IF;
"""
    + _BALANCE_BODY
    + """
    IF total <> 0 OR entries < 2 THEN
        RAISE EXCEPTION 'ledger transaction % does not balance (sum %, % entries)',
            target, total, entries USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER ledger_transaction_balance_check
    AFTER INSERT ON ledger_ledgertransaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_transaction_balances();
CREATE CONSTRAINT TRIGGER ledger_entry_balance_check
    AFTER INSERT ON ledger_ledgerentry
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_transaction_balances();
"""
)
DROP_BALANCED = """
DROP TRIGGER ledger_entry_balance_check ON ledger_ledgerentry;
DROP TRIGGER ledger_transaction_balance_check ON ledger_ledgertransaction;
DROP FUNCTION ledger_transaction_balances();
"""

APPEND_ONLY = (
    "REVOKE UPDATE, DELETE ON ledger_ledgertransaction, ledger_ledgerentry, ledger_receipt "
    "FROM app_rw;"
)
REVERSE_APPEND_ONLY = (
    "GRANT UPDATE, DELETE ON ledger_ledgertransaction, ledger_ledgerentry, ledger_receipt "
    "TO app_rw;"
)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organizations", "0002_security"),
    ]

    operations = [
        migrations.CreateModel(
            name="LedgerTransaction",
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
                            ("receipt", "Receipt"),
                            ("payment", "Payment"),
                            ("allocation", "Allocation"),
                            ("fine_assessed", "Fine Assessed"),
                            ("fine_waived", "Fine Waived"),
                            ("loan_disbursed", "Loan Disbursed"),
                            ("loan_repayment", "Loan Repayment"),
                            ("interest_charged", "Interest Charged"),
                            ("expense", "Expense"),
                            ("shareout", "Shareout"),
                            ("opening_balance", "Opening Balance"),
                            ("reversal", "Reversal"),
                        ],
                        max_length=32,
                    ),
                ),
                ("key", models.CharField(max_length=120)),
                ("member_id", models.UUIDField(blank=True, null=True)),
                ("memo", models.CharField(blank=True, default="", max_length=200)),
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
        ),
        migrations.CreateModel(
            name="LedgerEntry",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "ledger_account",
                    models.CharField(
                        choices=[
                            ("cash_mpesa", "Cash Mpesa"),
                            ("cash_bank", "Cash Bank"),
                            ("unallocated", "Unallocated"),
                            ("member_savings", "Member Savings"),
                            ("fines_receivable", "Fines Receivable"),
                            ("loans_receivable", "Loans Receivable"),
                            ("interest_receivable", "Interest Receivable"),
                            ("fine_income", "Fine Income"),
                            ("interest_income", "Interest Income"),
                            ("expenses", "Expenses"),
                            ("opening_equity", "Opening Equity"),
                        ],
                        max_length=32,
                    ),
                ),
                ("amount_minor", models.BigIntegerField()),
                ("member_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        db_constraint=False,
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="entries",
                        to="ledger.ledgertransaction",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Receipt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "channel",
                    models.CharField(choices=[("bank", "Bank"), ("mpesa", "Mpesa")], max_length=8),
                ),
                ("reference", models.CharField(max_length=40)),
                ("amount_minor", models.BigIntegerField()),
                ("received_on", models.DateField()),
                ("member_id", models.UUIDField(blank=True, null=True)),
                ("note", models.CharField(blank=True, default="", max_length=200)),
                ("recorded_by_member_id", models.UUIDField()),
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
        ),
        migrations.AddIndex(
            model_name="ledgertransaction",
            index=models.Index(
                fields=["chama", "member_id", "created_at"], name="ledger_txn_member_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgertransaction",
            constraint=models.UniqueConstraint(
                fields=("chama", "id"), name="ledger_txn_chama_id_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgertransaction",
            constraint=models.UniqueConstraint(fields=("chama", "key"), name="ledger_txn_key_uniq"),
        ),
        migrations.AddConstraint(
            model_name="ledgertransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "kind__in",
                        [
                            "receipt",
                            "payment",
                            "allocation",
                            "fine_assessed",
                            "fine_waived",
                            "loan_disbursed",
                            "loan_repayment",
                            "interest_charged",
                            "expense",
                            "shareout",
                            "opening_balance",
                            "reversal",
                        ],
                    )
                ),
                name="ledger_txn_kind_valid",
            ),
        ),
        migrations.AddIndex(
            model_name="ledgerentry",
            index=models.Index(fields=["chama", "transaction"], name="ledger_entry_txn_idx"),
        ),
        migrations.AddIndex(
            model_name="ledgerentry",
            index=models.Index(
                fields=["chama", "member_id", "ledger_account"], name="ledger_entry_member_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="ledgerentry",
            index=models.Index(fields=["chama", "ledger_account"], name="ledger_entry_acct_idx"),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_minor", 0), _negated=True),
                name="ledger_entry_amount_nonzero",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "ledger_account__in",
                        [
                            "cash_mpesa",
                            "cash_bank",
                            "unallocated",
                            "member_savings",
                            "fines_receivable",
                            "loans_receivable",
                            "interest_receivable",
                            "fine_income",
                            "interest_income",
                            "expenses",
                            "opening_equity",
                        ],
                    )
                ),
                name="ledger_entry_account_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        (
                            "ledger_account__in",
                            [
                                "member_savings",
                                "fines_receivable",
                                "loans_receivable",
                                "interest_receivable",
                            ],
                        ),
                        _negated=True,
                    ),
                    ("member_id__isnull", False),
                    _connector="OR",
                ),
                name="ledger_entry_member_required",
            ),
        ),
        migrations.AddIndex(
            model_name="receipt",
            index=models.Index(fields=["chama", "created_at"], name="ledger_receipt_created_idx"),
        ),
        migrations.AddConstraint(
            model_name="receipt",
            constraint=models.UniqueConstraint(
                fields=("chama", "id"), name="ledger_receipt_chama_id_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="receipt",
            constraint=models.UniqueConstraint(
                fields=("chama", "channel", "reference"), name="ledger_receipt_reference_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="receipt",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_minor__gt", 0)), name="ledger_receipt_amount_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="receipt",
            constraint=models.CheckConstraint(
                condition=models.Q(("channel__in", ["bank", "mpesa"])),
                name="ledger_receipt_channel_valid",
            ),
        ),
        chama_rls("ledger_ledgertransaction"),
        chama_rls("ledger_ledgerentry"),
        chama_rls("ledger_receipt"),
        migrations.RunSQL(FOREIGN_KEYS, DROP_FOREIGN_KEYS),
        migrations.RunSQL(BALANCED, DROP_BALANCED),
        migrations.RunSQL(APPEND_ONLY, REVERSE_APPEND_ONLY),
    ]
