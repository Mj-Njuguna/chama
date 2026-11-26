"""WhatsApp Cloud API (Meta, direct) adapter and webhook helpers (internal).

Built only from Meta's published Cloud API documentation:

- send: ``POST https://graph.facebook.com/{version}/{phone-number-id}/messages`` with
  ``Authorization: Bearer <token>`` and a JSON template message (``messaging_product``,
  ``recipient_type``, ``to``, ``type`` = ``template``, ``template.name``,
  ``template.language.code``, ``template.components`` with positional body parameters). A
  success answer carries ``messages[0].id`` (the wamid); acceptance is not delivery.
- errors: ``{"error": {"code": ...}}``. The codes in :data:`UNDELIVERABLE_CODES` mean WhatsApp
  cannot reach this member, so the router falls back to SMS; anything else is a failure.
- status webhooks: ``entry[].changes[].value.statuses[]`` with ``id``, ``status`` and, on
  failure, ``errors[].code``. Verification: ``hub.mode`` / ``hub.verify_token`` /
  ``hub.challenge`` on GET; ``X-Hub-Signature-256: sha256=<hex HMAC-SHA256(raw body, app
  secret)>`` on POST.

Disabled unless ``WHATSAPP_PHONE_NUMBER_ID`` and ``WHATSAPP_ACCESS_TOKEN`` are configured (and
the sender path is set in ``MESSAGE_SENDERS``). Template approval in Meta's manager is outside
the code: names and parameter counts are in :data:`apps.notifications.messaging.TEMPLATES`.
"""

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from apps.notifications.messaging import Outcome, SendResult

GRAPH_BASE = "https://graph.facebook.com"
# Meta: not a WhatsApp number (131026), outside the 24 h window (131047), not delivered to
# protect ecosystem engagement (131049), opted out of marketing (131050), business blocked by
# the user (130403), sender equals recipient (131021), unsupported message type (131051).
UNDELIVERABLE_CODES = frozenset({131026, 131047, 131049, 131050, 130403, 131021, 131051})


def build_payload(
    phone_e164: str, template: str, language: str, params: list[str]
) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_e164,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ],
        },
    }


def classify_error(body: bytes) -> SendResult:
    try:
        code = int(json.loads(body)["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return SendResult(Outcome.FAILED, error_code="unparsed")
    outcome = Outcome.UNDELIVERABLE if code in UNDELIVERABLE_CODES else Outcome.FAILED
    return SendResult(outcome, error_code=str(code))


class WhatsAppCloudSender:
    """Real sender. Never logs the number, the token or the message."""

    def send_template(
        self, phone_e164: str, template: str, params: list[str], *, timeout: float
    ) -> SendResult:
        phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
        token = settings.WHATSAPP_ACCESS_TOKEN
        if not phone_id or not token:
            return SendResult(Outcome.FAILED, error_code="not_configured")
        url = f"{GRAPH_BASE}/{settings.WHATSAPP_API_VERSION}/{phone_id}/messages"
        payload = build_payload(phone_e164, template, settings.WHATSAPP_TEMPLATE_LANGUAGE, params)
        request = urllib.request.Request(  # noqa: S310  fixed https URL
            url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = json.loads(response.read())
        except urllib.error.HTTPError as error:
            return classify_error(error.read())
        except (urllib.error.URLError, TimeoutError, ValueError):
            return SendResult(Outcome.FAILED, error_code="network")
        try:
            return SendResult(Outcome.ACCEPTED, provider_message_id=str(body["messages"][0]["id"]))
        except (KeyError, IndexError, TypeError):
            return SendResult(Outcome.FAILED, error_code="unparsed")


def signature_valid(raw_body: bytes, header: str | None) -> bool:
    secret = settings.WHATSAPP_APP_SECRET
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def statuses(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """``(wamid, status, first error code)`` for every status in a webhook payload."""
    found: list[tuple[str, str, str]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for item in (change.get("value") or {}).get("statuses") or []:
                errors = item.get("errors") or [{}]
                code = str(errors[0].get("code", "")) if errors else ""
                if item.get("id") and item.get("status"):
                    found.append((str(item["id"]), str(item["status"]), code))
    return found
