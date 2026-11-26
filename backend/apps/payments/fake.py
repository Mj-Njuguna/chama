"""Fake M-Pesa gateway for development and tests. Never used in production (a system check
refuses it outside DEBUG/tests).

It speaks its OWN simple callback format, ``{"fake_stk": {...}}``, not Daraja's: the real
callback format lives only in the (unverified) Daraja adapter.

- ``stk_push`` accepts every request and returns new ids. In development, when
  ``FAKE_MPESA_AUTOCOMPLETE_SECONDS`` is set, a Celery task posts a successful fake callback
  after that delay, so the app's payment modal can be demonstrated end to end.
- :func:`stk_callback_body` builds a callback body for tests and the simulator.
"""

import secrets
import uuid
from typing import Any

from apps.payments.gateway import Credentials, StkAccepted, StkResult

OUTCOMES = {"0": "succeeded", "1032": "cancelled", "1037": "timeout"}  # fake codes only

# Calls made, for tests to inspect: (method, kwargs).
CALLS: list[tuple[str, dict[str, Any]]] = []


def fake_receipt() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "F" + "".join(secrets.choice(alphabet) for _ in range(9))


def stk_callback_body(
    checkout_request_id: str,
    *,
    result_code: str = "0",
    receipt: str | None = None,
    amount_minor: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"checkout_request_id": checkout_request_id, "result_code": result_code}
    if result_code == "0":
        body["receipt"] = receipt or fake_receipt()
        body["amount_minor"] = amount_minor
    return {"fake_stk": body}


class FakeMpesaGateway:
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
        accepted = StkAccepted(f"fake-ck-{uuid.uuid4().hex}", f"fake-mr-{uuid.uuid4().hex}")
        CALLS.append(
            (
                "stk_push",
                {
                    "shortcode": credentials.shortcode,
                    "amount_minor": amount_minor,
                    "account_reference": account_reference,
                    "callback_url": callback_url,
                    "checkout_request_id": accepted.checkout_request_id,
                },
            )
        )
        return accepted

    def parse_stk_callback(self, body: Any) -> StkResult:
        data = body.get("fake_stk") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("checkout_request_id"), str):
            raise ValueError("not a fake STK callback")
        code = str(data.get("result_code", ""))
        outcome = OUTCOMES.get(code, "failed")
        amount = data.get("amount_minor")
        return StkResult(
            checkout_request_id=data["checkout_request_id"],
            outcome=outcome,
            result_code=code,
            mpesa_receipt=data.get("receipt") if outcome == "succeeded" else None,
            amount_minor=amount if isinstance(amount, int) else None,
        )

    def query_stk(self, credentials: Credentials, checkout_request_id: str) -> StkResult | None:
        return None

    def register_c2b_urls(
        self, credentials: Credentials, *, confirmation_url: str, validation_url: str
    ) -> None:
        CALLS.append(("register_c2b_urls", {"shortcode": credentials.shortcode}))
