"""Double-entry ledger. Internal: other modules use ``apps.ledger.services``.

- A ``LedgerTransaction`` is one business event (a receipt, a fine, a loan disbursement, ...).
  Its ``key`` is unique per chama, so posting the same event twice records nothing.
- Its ``LedgerEntry`` rows carry signed amounts in KES minor units: **debits positive, credits
  negative**. Every transaction sums to zero and has at least two entries, checked by the
  database at commit.
- Append-only: the app role may only insert; corrections are compensating transactions.
- Non-custodial: the cash accounts record money as it sits in the chama's OWN paybill, till or
  bank account. The platform never moves it.

Entries reference members by membership id, backed by a composite foreign key in SQL; no ORM
relation crosses the module boundary.
"""

import uuid
from typing import ClassVar

from django.db import models


class LedgerAccount(models.TextChoices):
    """The chart of accounts, the same for every chama."""

    CASH_MPESA = "cash_mpesa"  # asset: the chama's own M-Pesa paybill or till
    CASH_BANK = "cash_bank"  # asset: the chama's own bank account
    UNALLOCATED = "unallocated"  # liability: received, not yet linked to a member
    MEMBER_SAVINGS = "member_savings"  # liability: what the chama owes each member (per member)
    FINES_RECEIVABLE = "fines_receivable"  # asset: fines a member owes (per member)
    LOANS_RECEIVABLE = "loans_receivable"  # asset: loan principal a member owes (per member)
    INTEREST_RECEIVABLE = "interest_receivable"  # asset: loan interest a member owes (per member)
    FINE_INCOME = "fine_income"  # income
    INTEREST_INCOME = "interest_income"  # income
    EXPENSES = "expenses"  # expense: welfare payouts, bank charges, ...
    OPENING_EQUITY = "opening_equity"  # equity: balances that existed before the app


# Accounts that always say which member they belong to.
PER_MEMBER_ACCOUNTS = (
    LedgerAccount.MEMBER_SAVINGS,
    LedgerAccount.FINES_RECEIVABLE,
    LedgerAccount.LOANS_RECEIVABLE,
    LedgerAccount.INTEREST_RECEIVABLE,
)
CASH_ACCOUNTS = (LedgerAccount.CASH_MPESA, LedgerAccount.CASH_BANK)


class TransactionKind(models.TextChoices):
    RECEIPT = "receipt"  # money in, recorded by an official (bank slip, cash deposit)
    PAYMENT = "payment"  # money in, from an M-Pesa confirmation
    ALLOCATION = "allocation"  # unallocated money assigned to a member
    FINE_ASSESSED = "fine_assessed"
    FINE_WAIVED = "fine_waived"
    LOAN_DISBURSED = "loan_disbursed"
    LOAN_REPAYMENT = "loan_repayment"
    INTEREST_CHARGED = "interest_charged"
    EXPENSE = "expense"
    SHAREOUT = "shareout"
    OPENING_BALANCE = "opening_balance"
    REVERSAL = "reversal"


class LedgerTransaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=32, choices=TransactionKind.choices)
    # The business event, e.g. "receipt:<id>": posting it again is a no-op.
    key = models.CharField(max_length=120)
    member_id = models.UUIDField(null=True, blank=True)  # the member it is mainly about
    memo = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="ledger_txn_chama_id_uniq"),
            models.UniqueConstraint(fields=["chama", "key"], name="ledger_txn_key_uniq"),
            models.CheckConstraint(
                condition=models.Q(kind__in=TransactionKind.values), name="ledger_txn_kind_valid"
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "member_id", "created_at"], name="ledger_txn_member_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class LedgerEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    transaction = models.ForeignKey(
        LedgerTransaction,
        on_delete=models.PROTECT,
        related_name="entries",
        db_constraint=False,
        db_index=False,
    )
    ledger_account = models.CharField(max_length=32, choices=LedgerAccount.choices)
    amount_minor = models.BigIntegerField()  # debit > 0, credit < 0
    member_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=~models.Q(amount_minor=0), name="ledger_entry_amount_nonzero"
            ),
            models.CheckConstraint(
                condition=models.Q(ledger_account__in=LedgerAccount.values),
                name="ledger_entry_account_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(ledger_account__in=[a.value for a in PER_MEMBER_ACCOUNTS])
                | models.Q(member_id__isnull=False),
                name="ledger_entry_member_required",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "transaction"], name="ledger_entry_txn_idx"),
            models.Index(
                fields=["chama", "member_id", "ledger_account"], name="ledger_entry_member_idx"
            ),
            models.Index(fields=["chama", "ledger_account"], name="ledger_entry_acct_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class ReceiptChannel(models.TextChoices):
    BANK = "bank"
    MPESA = "mpesa"


class Receipt(models.Model):
    """Money an official records by hand: a bank slip, or an M-Pesa payment that never reached
    the platform. The ledger posting (``receipt:<id>``) is made in the same transaction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    channel = models.CharField(max_length=8, choices=ReceiptChannel.choices)
    # The slip number or M-Pesa code, normalized to upper case without spaces.
    reference = models.CharField(max_length=40)
    amount_minor = models.BigIntegerField()
    received_on = models.DateField()  # Africa/Nairobi business date on the slip
    member_id = models.UUIDField(null=True, blank=True)  # None: lands in unallocated
    note = models.CharField(max_length=200, blank=True, default="")
    recorded_by_member_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "id"], name="ledger_receipt_chama_id_uniq"),
            models.UniqueConstraint(
                fields=["chama", "channel", "reference"], name="ledger_receipt_reference_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="ledger_receipt_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=ReceiptChannel.values),
                name="ledger_receipt_channel_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "created_at"], name="ledger_receipt_created_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)
