"""Development only: simulate an M-Pesa C2B confirmation into a chama's linked shortcode.

Usage: ``just manage simulate_c2b <chama_id> 500 --bill-ref K7Q2MX`` (amount in KES).

It builds a body with the CONFIRMED C2B field names and runs the same storage and processing
as a real callback. Refuses to run where fakes are not allowed.
"""

import secrets
import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.payments.linking import active_credentials
from apps.payments.models import CallbackEvent, CallbackKind
from apps.payments.tasks import process_callback
from core.tenancy import chama_context


class Command(BaseCommand):
    help = "Development only: record a fake C2B confirmation for a chama."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("chama_id")
        parser.add_argument("amount_kes", type=Decimal)
        parser.add_argument("--bill-ref", default="", help="Account number the payer typed.")
        parser.add_argument("--receipt", default="", help="M-Pesa receipt (TransID).")
        parser.add_argument("--msisdn", default="2547 *** 000", help="Masked payer number.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.MESSAGING_ALLOW_FAKE:
            raise CommandError("simulate_c2b runs only where fakes are allowed (dev, tests)")
        chama_id = uuid.UUID(options["chama_id"])
        receipt = options["receipt"] or "S" + secrets.token_hex(5).upper()[:9]
        with chama_context(chama_id):
            linked = active_credentials()
            if linked is None:
                raise CommandError("the chama has no active payment linking")
            body = {
                "TransactionType": "Pay Bill" if linked[0].channel == "paybill" else "Buy Goods",
                "TransID": receipt,
                "TransTime": timezone.localtime().strftime("%Y%m%d%H%M%S"),
                "TransAmount": f"{options['amount_kes']:.2f}",
                "BusinessShortCode": linked[0].shortcode,
                "BillRefNumber": options["bill_ref"],
                "InvoiceNumber": "",
                "OrgAccountBalance": "",
                "ThirdPartyTransID": "",
                "MSISDN": options["msisdn"],
                "FirstName": "Test",
                "MiddleName": "",
                "LastName": "",
            }
            event = CallbackEvent.objects.create(
                chama_id=chama_id, kind=CallbackKind.C2B_CONFIRMATION, body=body
            )
        outcome = process_callback.delay(chama_id=str(chama_id), event_id=str(event.id))
        self.stdout.write(f"{receipt}: {getattr(outcome, 'result', outcome)}")
