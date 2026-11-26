"""Member messages (WhatsApp first, SMS on undeliverable), the WhatsApp webhook, the feed and the
PaymentConfirmed subscriber."""

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from django.test import Client

from apps.contributions.models import Obligation
from apps.notifications import fake
from apps.notifications.messaging import deliver
from apps.notifications.models import FeedEvent, Message, ProviderMessageRef
from apps.notifications.services import publish_feed_event, send_member_message
from apps.notifications.whatsapp import build_payload, classify_error
from apps.payments.events import PaymentAllocated, PaymentConfirmed
from core.events import publish
from core.tenancy import chama_context
from tests.test_contributions import World, _contribution, _get, _login, _user_of

pytestmark = pytest.mark.django_db
WEBHOOK = "/api/v1/notifications/whatsapp/webhook"


@pytest.fixture(autouse=True)
def _fakes(fake_senders):
    fake.MESSAGE_BEHAVIOUR.clear()
    yield fake_senders
    fake.MESSAGE_BEHAVIOUR.clear()


@pytest.fixture
def world() -> World:
    return World(phone_prefix="+2547120005")


def _send(world: World, member: uuid.UUID, key: str, capture: Any) -> Message:
    with capture(execute=True), world.ctx():
        send_member_message(world.chama.id, member, "fine_notice", ["Umoja", "KES 200.00"], key)
    with world.ctx():
        return Message.objects.get(idempotency_key=key)


def test_whatsapp_first(world, django_capture_on_commit_callbacks):
    message = _send(world, world.alice, "k1", django_capture_on_commit_callbacks)
    assert (message.status, message.channel) == ("sent", "whatsapp")
    assert ProviderMessageRef.objects.filter(message_id=message.id).exists()


def test_sms_fallback_only_when_whatsapp_is_undeliverable(
    world, django_capture_on_commit_callbacks
):
    alice = world.phones[str(world.alice)]
    fake.MESSAGE_BEHAVIOUR[("whatsapp", alice)] = "undeliverable"
    message = _send(world, world.alice, "k2", django_capture_on_commit_callbacks)
    assert (message.status, message.channel) == ("sent", "sms")
    assert fake.message_outbox(alice)[0]["channel"] == "sms"

    treasurer = world.phones[str(world.treasurer)]
    fake.MESSAGE_BEHAVIOUR[("whatsapp", treasurer)] = "failed"
    failed = _send(world, world.treasurer, "k3", django_capture_on_commit_callbacks)
    assert (failed.status, failed.channel) == ("failed", "whatsapp")
    assert fake.message_outbox(treasurer) == []


def test_messages_are_idempotent_and_need_consent(world, django_capture_on_commit_callbacks):
    _send(world, world.alice, "k4", django_capture_on_commit_callbacks)
    with world.ctx():
        again = send_member_message(world.chama.id, world.alice, "fine_notice", ["a", "b"], "k4")
        assert again is False
        assert Message.objects.filter(idempotency_key="k4").count() == 1
    skipped = _send(world, world.bob, "k5", django_capture_on_commit_callbacks)
    assert (skipped.status, skipped.error_code) == ("skipped", "no_consent")


def test_template_parameters_are_checked(world):
    with world.ctx(), pytest.raises(ValueError):
        send_member_message(world.chama.id, world.alice, "fine_notice", ["only one"], "k6")


def test_a_claimed_message_is_not_sent_twice(world, django_capture_on_commit_callbacks):
    message = _send(world, world.alice, "k7", django_capture_on_commit_callbacks)
    assert deliver(world.chama.id, message.id) == "not_claimed"
    assert len(fake.message_outbox(world.phones[str(world.alice)])) == 1


# ---------------------------------------------------------------- WhatsApp payloads and webhook


def test_payload_and_error_classification():
    payload = build_payload("+254712000501", "fine_notice", "en", ["Umoja", "KES 1.00"])
    assert payload["type"] == "template"
    assert payload["template"]["components"][0]["parameters"][1] == {
        "type": "text",
        "text": "KES 1.00",
    }
    assert classify_error(b'{"error": {"code": 131026}}').outcome == "undeliverable"
    assert classify_error(b'{"error": {"code": 132001}}').outcome == "failed"
    assert classify_error(b"not json").outcome == "failed"


def _signed(body: bytes) -> str:
    return "sha256=" + hmac.new(b"test-app-secret", body, hashlib.sha256).hexdigest()


def _status_payload(wamid: str, status: str, code: int | None = None) -> bytes:
    item: dict[str, Any] = {"id": wamid, "status": status, "timestamp": "1", "recipient_id": "x"}
    if code is not None:
        item["errors"] = [{"code": code, "title": "t"}]
    value = {"statuses": [item]}
    return json.dumps({"entry": [{"changes": [{"field": "messages", "value": value}]}]}).encode()


def test_webhook_subscription_check(client: Client):
    query = {
        "hub.mode": "subscribe",
        "hub.verify_token": "test-verify-token",
        "hub.challenge": "42",
    }
    ok = client.get(WEBHOOK, query)
    assert (ok.status_code, ok.content) == (200, b"42")
    wrong = client.get(
        WEBHOOK, {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}
    )
    assert wrong.status_code == 403


def test_webhook_refuses_a_bad_signature(client: Client):
    body = _status_payload("wamid.x", "delivered")
    for signature in (None, "sha256=00", _signed(body + b" ")):
        headers = {"X-Hub-Signature-256": signature} if signature else {}
        response = client.post(WEBHOOK, body, content_type="application/json", headers=headers)
        assert response.status_code == 401


def test_webhook_statuses_update_messages_and_fall_back_to_sms(
    client: Client, world, django_capture_on_commit_callbacks
):
    message = _send(world, world.alice, "k8", django_capture_on_commit_callbacks)
    body = _status_payload(message.provider_message_id, "delivered")
    headers = {"X-Hub-Signature-256": _signed(body)}
    posted = client.post(WEBHOOK, body, content_type="application/json", headers=headers)
    assert posted.status_code == 200
    with world.ctx():
        message.refresh_from_db()
    assert message.status == "delivered"

    other = _send(world, world.treasurer, "k9", django_capture_on_commit_callbacks)
    body = _status_payload(other.provider_message_id, "failed", 131026)
    headers = {"X-Hub-Signature-256": _signed(body)}
    response = client.post(WEBHOOK, body, content_type="application/json", headers=headers)
    assert response.status_code == 200
    with world.ctx():
        other.refresh_from_db()
    assert (other.status, other.channel) == ("sent", "sms")


# ---------------------------------------------------------------- feed and events


def test_payment_confirmed_adds_feed_entry_and_receipt(world, django_capture_on_commit_callbacks):
    payment = uuid.uuid4()
    with django_capture_on_commit_callbacks(execute=True), world.ctx():
        publish(PaymentConfirmed(world.chama.id, payment, world.alice, 150_000))
    with world.ctx():
        event = FeedEvent.objects.get(kind="payment_confirmed")
        assert event.payload["amount_minor"] == 150_000
        assert event.payload["member_id"] == str(world.alice)
        receipt = Message.objects.get(idempotency_key=f"payment:{payment}:receipt")
    assert receipt.template == "payment_receipt"
    assert receipt.params[1] == "KES 1,500.00"


def test_unallocated_then_allocated_payment(world, django_capture_on_commit_callbacks):
    payment = uuid.uuid4()
    with django_capture_on_commit_callbacks(execute=True), world.ctx():
        publish(PaymentConfirmed(world.chama.id, payment, None, 50_000))
    with world.ctx():
        assert Message.objects.count() == 0  # nobody to thank yet
        assert FeedEvent.objects.get().payload["member_id"] is None
    with django_capture_on_commit_callbacks(execute=True), world.ctx():
        publish(PaymentAllocated(world.chama.id, payment, world.alice, 50_000))
        publish(PaymentAllocated(world.chama.id, payment, world.alice, 50_000))
    with world.ctx():
        assert FeedEvent.objects.filter(kind="payment_confirmed").count() == 2
        assert Message.objects.get().idempotency_key == f"payment:{payment}:receipt"


def test_feed_pages_newest_first_and_is_per_chama(client: Client, world):
    with world.ctx():
        for i in range(3):
            publish_feed_event(world.chama.id, "cycle_opened", {"n": i}, key=f"e{i}")
        assert publish_feed_event(world.chama.id, "cycle_opened", {}, key="e0") is False
    alice = _login(_user_of(world, world.alice), world.chama)
    first = _get(client, "/api/v1/chama/feed?limit=2", alice).json()
    assert [e["payload"]["n"] for e in first["events"]] == [2, 1]
    rest = _get(client, f"/api/v1/chama/feed?limit=2&cursor={first['next_cursor']}", alice).json()
    assert [e["payload"]["n"] for e in rest["events"]] == [0]
    assert rest["next_cursor"] is None


def test_receipts_for_a_member_go_through_allocation(world):
    from datetime import date

    from apps.ledger.receipts import record_receipt
    from apps.ledger.services import LedgerAccount, member_balance

    with world.ctx():
        ob = _contribution(world, world.alice, date(2026, 11, 5))
        record_receipt(
            chama_id=world.chama.id,
            recorded_by_member_id=world.treasurer,
            channel="bank",
            reference="SLIP001",
            amount_minor=130_000,
            received_on=date(2026, 11, 20),
            member_id=world.alice,
        )
        ob.refresh_from_db()
        assert ob.status == "paid"
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, world.alice) == -130_000
        assert Obligation.objects.filter(member_id=world.alice).count() == 1
    with chama_context(world.chama.id):
        assert member_balance(LedgerAccount.CASH_BANK, world.alice) == 0  # cash is not per member
