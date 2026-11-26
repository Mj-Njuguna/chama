"""UNVERIFIED: confirm against the Daraja portal before enabling.

Safaricom Daraja adapter for :class:`apps.payments.gateway.MpesaGateway`.

Every Daraja field name and endpoint for OAuth, M-Pesa Express (STK Push), STK Query and the
STK result callback is NOT CONFIRMED from an official source (the public API pages render no
documentation without a Daraja login). Nothing here is selected by any settings default: it is
enabled only by setting ``MPESA_GATEWAY = "apps.payments.daraja.DarajaGateway"`` explicitly, and
that must not happen until each item below is checked against the Daraja portal or a sandbox
run:

1. OAuth token endpoint, method, auth scheme, response fields and lifetime.
2. STK Push endpoint and request fields (BusinessShortCode, Password construction, Timestamp
   format, TransactionType for paybill vs till, Amount, PartyA, PartyB, PhoneNumber,
   CallBackURL, AccountReference and TransactionDesc limits) and the response fields.
3. The STK result callback structure and the meaning of every ResultCode.
4. STK Query endpoint and fields.
5. C2B Register URL request (``POST /mpesa/c2b/v2/registerurl`` with ShortCode, ResponseType,
   ConfirmationURL, ValidationURL is CONFIRMED; the OAuth step it needs is not).

Until then this class refuses to work, so an accidental configuration fails loudly instead of
sending guessed requests to Safaricom.
"""

from typing import Any

from apps.payments.gateway import Credentials, GatewayError, StkAccepted, StkResult

_UNVERIFIED = "Daraja adapter is UNVERIFIED: confirm the field mapping before enabling it"


class DarajaGateway:
    def stk_push(
        self,
        credentials: Credentials,
        *,
        phone_e164: str,
        amount_minor: int,
        account_reference: str,
        description: str,
        callback_url: str,
    ) -> StkAccepted:
        raise GatewayError(_UNVERIFIED)

    def parse_stk_callback(self, body: Any) -> StkResult:
        raise ValueError(_UNVERIFIED)

    def query_stk(self, credentials: Credentials, checkout_request_id: str) -> StkResult | None:
        raise GatewayError(_UNVERIFIED)

    def register_c2b_urls(
        self, credentials: Credentials, *, confirmation_url: str, validation_url: str
    ) -> None:
        raise GatewayError(_UNVERIFIED)
