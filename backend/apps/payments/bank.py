"""Bank notification adapters (interface only).

Banks (KCB, Equity, Co-op, NCBA) do not send Daraja callbacks for their paybills. When a bank's
notification API is confirmed, an adapter implements :class:`BankNotificationProvider` and feeds
:class:`BankCredit` values into the same confirmation transaction as M-Pesa payments
(``debit_account="cash_bank"``). Nothing else changes. No adapter exists yet.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class BankCredit:
    bank: str  # e.g. "kcb", "equity", "coop", "ncba"
    reference: str  # the bank's unique transaction reference
    amount_minor: int
    received_at: datetime
    account_reference: str  # what the payer typed as the account (member code if any)
    payer_masked: str = ""


class BankNotificationProvider(Protocol):
    bank: str

    def parse_notification(self, body: Any, headers: dict[str, str]) -> BankCredit:
        """Verify and parse one notification. Raise ValueError if it is not authentic."""
        ...
