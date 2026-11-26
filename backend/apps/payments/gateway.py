"""The M-Pesa gateway interface (internal). Provider details never leak past this module.

``settings.MPESA_GATEWAY`` names a zero-argument factory (dotted path). Empty means payments are
not available on this deployment: linking can still be saved, STK requests answer 503.
"""

from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

from django.conf import settings
from django.utils.module_loading import import_string


class GatewayError(Exception):
    """The provider refused the request or could not be reached."""


@dataclass(frozen=True)
class Credentials:
    channel: str  # paybill | till
    shortcode: str
    environment: str  # sandbox | production
    consumer_key: str
    consumer_secret: str
    passkey: str


@dataclass(frozen=True)
class StkAccepted:
    checkout_request_id: str
    merchant_request_id: str


@dataclass(frozen=True)
class StkResult:
    checkout_request_id: str
    outcome: str  # succeeded | failed | cancelled | timeout
    result_code: str
    mpesa_receipt: str | None = None
    amount_minor: int | None = None


class MpesaGateway(Protocol):
    def stk_push(
        self,
        credentials: Credentials,
        *,
        phone_e164: str,
        amount_minor: int,
        account_reference: str,
        description: str,
        callback_url: str,
    ) -> StkAccepted: ...

    def parse_stk_callback(self, body: Any) -> StkResult:
        """Raise ValueError if ``body`` is not a callback this gateway understands."""
        ...

    def query_stk(self, credentials: Credentials, checkout_request_id: str) -> StkResult | None:
        """The final result of a request, or None if unknown or still pending."""
        ...

    def register_c2b_urls(
        self, credentials: Credentials, *, confirmation_url: str, validation_url: str
    ) -> None: ...


def gateway_configured() -> bool:
    return bool(settings.MPESA_GATEWAY)


@cache
def _factory(path: str) -> Any:
    return import_string(path)


def get_gateway() -> MpesaGateway:
    if not settings.MPESA_GATEWAY:
        raise GatewayError("no M-Pesa gateway configured")
    gateway: MpesaGateway = _factory(settings.MPESA_GATEWAY)()
    return gateway
