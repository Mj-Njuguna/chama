"""Payments: linking, C2B confirmations, STK prompts, the confirmation transaction, the
unallocated resolver and isolation. All providers are fakes; no network."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.ledger.services import LedgerAccount, account_balance, member_balance
from apps.notifications import fake as fake_messages
from apps.organizations.models import Membership
from apps.payments import fake
from apps.payments.confirm import record_payment
from apps.payments.linking import token_hash
from apps.payments.models import (
    CallbackRoute,
    LinkingStatus,
    Payment,
    PaymentLinking,
    StkRequest,
    StkStatus,
)
from apps.payments.stk import sweep_stale_requests
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

CHAIR_PHONE = "+254712000301"
MEMBER_PHONE = "+254712000302"
PIN = "275931"
SHORTCODE = "600123"


@pytest.fixture(autouse=True)
def _fakes(fake_senders):
    fake.CALLS.clear()
    return fake_senders


def _h(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


def _call(
    client: Client,
    method: str,
    url: str,
    pair: TokenPair,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return getattr(client, method)(
        url, body or {}, content_type="application/json", headers={**_h(pair), **(headers or {})}
    )


def _login(phone: str, installation: str) -> TokenPair:
    user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
    return sessions.login(user.id, DeviceInfo(installation, "android"), None)


def _fresh_otp(client: Client, pair: TokenPair, phone: str) -> None:
    sent = _call(client, "post", "/api/v1/auth/step-up/otp/request", pair)
    assert sent.status_code == 202, sent.content
    code = fake_messages.outbox(phone)[-1]["code"]
    body = {"challenge_id": sent.json()["challenge_id"], "code": code}
    assert _call(client, "post", "/api/v1/auth/step-up/otp/verify", pair, body).status_code == 200


def _fresh_pin(client: Client, pair: TokenPair, phone: str) -> None:
    _fresh_otp(client, pair, phone)
    assert _call(client, "put", "/api/v1/auth/pin", pair, {"pin": PIN}).status_code == 204
    ok = _call(client, "post", "/api/v1/auth/step-up/pin", pair, {"pin": PIN})
    assert ok.status_code in (200, 204), ok.content


def _new_chama(client: Client, phone: str, installation: str) -> tuple[TokenPair, uuid.UUID]:
    pair = _login(phone, installation)
    created = _call(client, "post", "/api/v1/chamas", pair, {"name": "Umoja"})
    assert created.status_code == 201, created.content
    return pair, uuid.UUID(created.json()["chama_id"])


def _add_member(chama_id: uuid.UUID, phone: str) -> Membership:
    user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
    with chama_context(chama_id):
        return Membership.objects.create(chama_id=chama_id, user=user, role="member")


def _link(chama_id: uuid.UUID, channel: str = "paybill", shortcode: str = SHORTCODE) -> str:
    """Active linking with a known callback token (bypasses the API)."""
    token = f"tok-{uuid.uuid4().hex}"
    with chama_context(chama_id):
        CallbackRoute.objects.create(token_hash=token_hash(token), chama_id=chama_id)
        PaymentLinking.objects.create(
            chama_id=chama_id,
            status=LinkingStatus.ACTIVE,
            channel=channel,
            shortcode=shortcode,
            environment="sandbox",
            consumer_key="ck",
            consumer_secret="cs",
            passkey="pk",
            callback_token=token,
            requested_by_user_id=uuid.uuid4(),
            activated_at=timezone.now(),
        )
    return token


@pytest.fixture
def world(client) -> dict[str, Any]:
    pair, chama_id = _new_chama(client, CHAIR_PHONE, "install-pay-chair")
    member = _add_member(chama_id, MEMBER_PHONE)
    return {
        "pair": pair,
        "chama_id": chama_id,
        "member": member,
        "token": _link(chama_id),
    }


def _c2b(receipt: str = "SKL4H2JQ9P", amount: str = "500.00", **fields: str) -> dict[str, str]:
    return {
        "TransactionType": "Pay Bill",
        "TransID": receipt,
        "TransTime": "20261126101500",
        "TransAmount": amount,
        "BusinessShortCode": SHORTCODE,
        "BillRefNumber": "",
        "MSISDN": "2547 *** 302",
        "FirstName": "Wanjiru",
        **fields,
    }


def _confirm(client, django_capture_on_commit_callbacks, token: str, body: dict[str, Any]) -> Any:
    with django_capture_on_commit_callbacks(execute=True):
        return client.post(
            f"/api/v1/payments/c2b/{token}/confirmation", body, content_type="application/json"
        )


def _payments(chama_id: uuid.UUID) -> list[Payment]:
    with chama_context(chama_id):
        return list(Payment.objects.all())


# ---------------------------------------------------------------- linking


def test_linking_needs_a_fresh_otp_and_never_returns_secrets(client):
    pair, chama_id = _new_chama(client, "+254712000311", "install-pay-link")
    assert _call(client, "get", "/api/v1/chama/payment-linking", pair).json()["status"] == (
        "not_linked"
    )
    body = {
        "channel": "paybill",
        "shortcode": "600999",
        "environment": "sandbox",
        "consumer_key": "key-SECRET-1",
        "consumer_secret": "secret-SECRET-2",
        "passkey": "pass-SECRET-3",
    }
    refused = _call(client, "put", "/api/v1/chama/payment-linking", pair, body)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "step_up_required"

    _fresh_otp(client, pair, "+254712000311")
    with_otp = _call(client, "put", "/api/v1/chama/payment-linking", pair, body)
    assert with_otp.status_code == 200, with_otp.content
    assert with_otp.json()["status"] == "active"
    shown = _call(client, "get", "/api/v1/chama/payment-linking", pair)
    for response in (with_otp, shown):
        assert "SECRET" not in response.content.decode()
        assert response.json()["credentials_set"] is True
    with chama_context(chama_id), connection.cursor() as cursor:
        cursor.execute("SELECT consumer_key, consumer_secret, passkey FROM payments_paymentlinking")
        stored = cursor.fetchone()
    assert all(value.startswith("fe1:") and "SECRET" not in value for value in stored)
    with chama_context(chama_id):
        assert CallbackRoute.objects.filter(chama_id=chama_id).count() == 1

    # Replacing active details waits out a cooling-off.
    replaced = _call(
        client, "put", "/api/v1/chama/payment-linking", pair, {**body, "shortcode": "600888"}
    )
    assert replaced.status_code == 200, replaced.content
    assert replaced.json()["status"] == "active"
    assert replaced.json()["shortcode"] == "600999"
    assert replaced.json()["pending_shortcode"] == "600888"
    assert replaced.json()["pending_change_id"] is not None
    early = _call(client, "post", "/api/v1/chama/payment-linking/apply", pair)
    assert early.status_code == 403
    assert early.json()["error"]["code"] == "cooling_off"


def test_members_cannot_see_or_change_linking(client, world):
    member_pair = _login(MEMBER_PHONE, "install-pay-member-a")
    _call(
        client,
        "post",
        "/api/v1/auth/switch-chama",
        member_pair,
        {"chama_id": str(world["chama_id"])},
    )
    assert _call(client, "get", "/api/v1/chama/payment-linking", member_pair).status_code == 404
    put = _call(client, "put", "/api/v1/chama/payment-linking", member_pair, {})
    assert put.status_code == 403


# ---------------------------------------------------------------- C2B confirmations


def test_member_code_matches_and_credits_savings(client, world, django_capture_on_commit_callbacks):
    code = world["member"].member_code
    response = _confirm(
        client, django_capture_on_commit_callbacks, world["token"], _c2b(BillRefNumber=code.lower())
    )
    assert response.status_code == 200
    assert response.json() == {"ResultCode": "0", "ResultDesc": "Accepted"}
    [payment] = _payments(world["chama_id"])
    assert payment.amount_minor == 50_000
    with chama_context(world["chama_id"]):
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, world["member"].id) == -50_000
        assert account_balance(LedgerAccount.CASH_MPESA) == 50_000
        assert account_balance(LedgerAccount.UNALLOCATED) == 0


def test_duplicate_confirmation_is_a_no_op(client, world, django_capture_on_commit_callbacks):
    body = _c2b(BillRefNumber=world["member"].member_code)
    for _ in range(3):
        assert (
            _confirm(client, django_capture_on_commit_callbacks, world["token"], body).status_code
            == 200
        )
    assert len(_payments(world["chama_id"])) == 1
    with chama_context(world["chama_id"]):
        assert account_balance(LedgerAccount.CASH_MPESA) == 50_000


def test_a_concurrent_duplicate_falls_back_on_the_unique_receipt(world):
    """The loser of a race hits the unique constraint (no pre-check): it records nothing."""
    chama_id, member = world["chama_id"], world["member"].id
    with chama_context(chama_id):
        first = record_payment(
            chama_id,
            source="c2b",
            mpesa_receipt="RACE123456",
            amount_minor=10_000,
            received_at=timezone.now(),
            member_id=member,
        )
        second = record_payment(
            chama_id,
            source="c2b",
            mpesa_receipt="RACE123456",
            amount_minor=10_000,
            received_at=timezone.now(),
            member_id=member,
        )
        assert first.created and not second.created
        assert second.payment_id == first.payment_id and second.member_id == member
        assert account_balance(LedgerAccount.CASH_MPESA) == 10_000


@pytest.mark.parametrize("bill_ref", ["", "NOTACODE", "rent june"])
def test_no_valid_code_lands_in_unallocated(
    client, world, django_capture_on_commit_callbacks, bill_ref
):
    _confirm(
        client, django_capture_on_commit_callbacks, world["token"], _c2b(BillRefNumber=bill_ref)
    )
    assert len(_payments(world["chama_id"])) == 1
    with chama_context(world["chama_id"]):
        assert account_balance(LedgerAccount.UNALLOCATED) == -50_000
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, world["member"].id) == 0


def test_till_payments_are_always_unallocated(client, django_capture_on_commit_callbacks):
    _, chama_id = _new_chama(client, "+254712000321", "install-pay-till")
    member = _add_member(chama_id, "+254712000322")
    token = _link(chama_id, channel="till")
    body = _c2b(TransactionType="Buy Goods", BillRefNumber=member.member_code)
    _confirm(client, django_capture_on_commit_callbacks, token, body)
    with chama_context(chama_id):
        assert account_balance(LedgerAccount.UNALLOCATED) == -50_000
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, member.id) == 0


def test_wrong_shortcode_or_bad_amount_records_nothing(
    client, world, django_capture_on_commit_callbacks
):
    token = world["token"]
    _confirm(client, django_capture_on_commit_callbacks, token, _c2b(BusinessShortCode="999999"))
    _confirm(
        client,
        django_capture_on_commit_callbacks,
        token,
        _c2b(receipt="SKL4H2JQ9Q", amount="10.505"),
    )
    _confirm(
        client, django_capture_on_commit_callbacks, token, _c2b(receipt="SKL4H2JQ9R", amount="-5")
    )
    assert _payments(world["chama_id"]) == []


def test_unknown_token_is_refused_and_tokens_cannot_cross_chamas(
    client, world, django_capture_on_commit_callbacks
):
    assert _confirm(client, django_capture_on_commit_callbacks, "nope", _c2b()).status_code == 404
    _, other = _new_chama(client, "+254712000331", "install-pay-other")
    other_token = _link(other)
    # A body naming chama A's shortcode, sent to chama B's URL, never lands in chama A.
    _confirm(
        client,
        django_capture_on_commit_callbacks,
        other_token,
        _c2b(BillRefNumber=world["member"].member_code),
    )
    assert _payments(world["chama_id"]) == []
    [landed] = _payments(other)
    with chama_context(other):
        assert account_balance(LedgerAccount.UNALLOCATED) == -50_000  # A's code means nothing in B
    assert landed.mpesa_receipt == "SKL4H2JQ9P"


# ---------------------------------------------------------------- STK prompts


def _member_session(client, world, installation: str) -> TokenPair:
    pair = _login(MEMBER_PHONE, installation)
    switched = _call(
        client, "post", "/api/v1/auth/switch-chama", pair, {"chama_id": str(world["chama_id"])}
    )
    assert switched.status_code == 200
    return pair


def _stk(client, pair, amount: int = 50_000, key: str | None = None, capture=None) -> Any:
    headers = {"Idempotency-Key": key} if key else None
    if capture is None:
        return _call(
            client, "post", "/api/v1/chama/payments/stk", pair, {"amount_minor": amount}, headers
        )
    with capture(execute=True):
        return _call(
            client, "post", "/api/v1/chama/payments/stk", pair, {"amount_minor": amount}, headers
        )


def _stk_result(client, capture, token: str, body: dict[str, Any]) -> Any:
    with capture(execute=True):
        return client.post(
            f"/api/v1/payments/stk-result/{token}", body, content_type="application/json"
        )


def _checkout(chama_id: uuid.UUID, stk_id: str) -> str:
    with chama_context(chama_id):
        return str(StkRequest.objects.get(id=stk_id).checkout_request_id)


def test_stk_success_records_the_payment_for_the_requester(
    client, world, django_capture_on_commit_callbacks
):
    pair = _member_session(client, world, "install-pay-stk1")
    made = _stk(client, pair, capture=django_capture_on_commit_callbacks)
    assert made.status_code == 202, made.content
    stk_id = made.json()["stk_request_id"]
    assert made.json()["status"] == "pending"
    sent = [c for c in fake.CALLS if c[0] == "stk_push"]
    assert sent and sent[0][1]["account_reference"] == world["member"].member_code
    assert sent[0][1]["callback_url"].endswith(f"/api/v1/payments/stk-result/{world['token']}")

    checkout = _checkout(world["chama_id"], stk_id)
    body = fake.stk_callback_body(checkout, receipt="SKM7Q2P8XA", amount_minor=50_000)
    assert (
        _stk_result(client, django_capture_on_commit_callbacks, world["token"], body).status_code
        == 200
    )
    _stk_result(client, django_capture_on_commit_callbacks, world["token"], body)  # repeated

    status = _call(client, "get", f"/api/v1/chama/payments/stk/{stk_id}", pair).json()
    assert status["status"] == "succeeded"
    assert status["mpesa_receipt"] == "SKM7Q2P8XA"
    assert len(_payments(world["chama_id"])) == 1
    with chama_context(world["chama_id"]):
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, world["member"].id) == -50_000


@pytest.mark.parametrize(
    ("code", "final"), [("1032", "cancelled"), ("1037", "timeout"), ("2001", "failed")]
)
def test_stk_failures_record_nothing(
    client, world, django_capture_on_commit_callbacks, code, final
):
    pair = _member_session(client, world, f"install-pay-stk-{code}")
    stk_id = _stk(client, pair, capture=django_capture_on_commit_callbacks).json()["stk_request_id"]
    body = fake.stk_callback_body(_checkout(world["chama_id"], stk_id), result_code=code)
    _stk_result(client, django_capture_on_commit_callbacks, world["token"], body)
    assert (
        _call(client, "get", f"/api/v1/chama/payments/stk/{stk_id}", pair).json()["status"] == final
    )
    assert _payments(world["chama_id"]) == []


def test_stale_requests_time_out_and_a_late_success_still_counts(
    client, world, django_capture_on_commit_callbacks
):
    pair = _member_session(client, world, "install-pay-stk-late")
    stk_id = _stk(client, pair, capture=django_capture_on_commit_callbacks).json()["stk_request_id"]
    with chama_context(world["chama_id"]):
        StkRequest.objects.filter(id=stk_id).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )
    assert sweep_stale_requests(world["chama_id"]) == 1
    assert (
        _call(client, "get", f"/api/v1/chama/payments/stk/{stk_id}", pair).json()["status"]
        == "timeout"
    )

    body = fake.stk_callback_body(
        _checkout(world["chama_id"], stk_id), receipt="SKLATE1234", amount_minor=50_000
    )
    _stk_result(client, django_capture_on_commit_callbacks, world["token"], body)
    with chama_context(world["chama_id"]):
        assert StkRequest.objects.get(id=stk_id).status == StkStatus.SUCCEEDED
    assert len(_payments(world["chama_id"])) == 1


def test_stk_idempotency_validation_and_linking(client, world, django_capture_on_commit_callbacks):
    pair = _member_session(client, world, "install-pay-stk-idem")
    first = _stk(client, pair, key="tap-1", capture=django_capture_on_commit_callbacks)
    again = _stk(client, pair, key="tap-1", capture=django_capture_on_commit_callbacks)
    assert first.json()["stk_request_id"] == again.json()["stk_request_id"]
    odd = _stk(client, pair, amount=50_050)
    assert odd.status_code == 400
    assert "amount_minor" in odd.json()["error"]["field_errors"]

    unlinked_pair, _ = _new_chama(client, "+254712000341", "install-pay-unlinked")
    refused = _stk(client, unlinked_pair)
    assert refused.status_code == 409
    assert refused.json()["error"]["reason"] == "payments_not_linked"


def test_stk_status_is_private_to_the_requester(client, world, django_capture_on_commit_callbacks):
    pair = _member_session(client, world, "install-pay-stk-own")
    stk_id = _stk(client, pair, capture=django_capture_on_commit_callbacks).json()["stk_request_id"]
    assert (
        _call(client, "get", f"/api/v1/chama/payments/stk/{stk_id}", world["pair"]).status_code
        == 404
    )


# ---------------------------------------------------------------- resolver and lists


def test_resolver_suggests_and_allocation_is_idempotent(
    client, world, django_capture_on_commit_callbacks
):
    code = world["member"].member_code
    typo = ("2" if code[0] != "2" else "3") + code[1:]
    _confirm(client, django_capture_on_commit_callbacks, world["token"], _c2b(BillRefNumber=typo))
    listed = _call(client, "get", "/api/v1/chama/payments/unallocated", world["pair"]).json()
    [row] = listed["payments"]
    reasons = {(s["member_id"], s["reason"]) for s in row["suggestions"]}
    member_id = str(world["member"].id)
    assert (member_id, "similar_code") in reasons
    assert row["payer_masked"] == "2547 *** 302"

    url = f"/api/v1/chama/payments/{row['payment_id']}/allocate"
    refused = _call(client, "post", url, world["pair"], {"member_id": member_id})
    assert refused.status_code == 403
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    for _ in range(2):
        done = _call(client, "post", url, world["pair"], {"member_id": member_id})
        assert done.status_code == 200, done.content
        assert done.json()["status"] == "allocated"
    with chama_context(world["chama_id"]):
        assert account_balance(LedgerAccount.UNALLOCATED) == 0
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, world["member"].id) == -50_000

    with chama_context(world["chama_id"]):
        chair_member = Membership.objects.exclude(id=world["member"].id).get()
    conflict = _call(client, "post", url, world["pair"], {"member_id": str(chair_member.id)})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["reason"] == "payment_already_allocated"
    assert (
        _call(client, "get", "/api/v1/chama/payments/unallocated", world["pair"]).json()["payments"]
        == []
    )


def test_payment_lists_are_scoped(client, world, django_capture_on_commit_callbacks):
    _confirm(
        client,
        django_capture_on_commit_callbacks,
        world["token"],
        _c2b(BillRefNumber=world["member"].member_code),
    )
    _confirm(client, django_capture_on_commit_callbacks, world["token"], _c2b(receipt="SKOTHER123"))
    officials = _call(client, "get", "/api/v1/chama/payments", world["pair"]).json()["payments"]
    assert len(officials) == 2
    mine = _call(client, "get", "/api/v1/chama/payments?mine=true", world["pair"]).json()[
        "payments"
    ]
    assert mine == []
    member_pair = _member_session(client, world, "install-pay-list")
    own = _call(client, "get", "/api/v1/chama/payments", member_pair).json()["payments"]
    assert [p["status"] for p in own] == ["allocated"]
    assert (
        _call(client, "get", "/api/v1/chama/payments/unallocated", member_pair).status_code == 404
    )

    other_pair, _ = _new_chama(client, "+254712000351", "install-pay-list-other")
    assert _call(client, "get", "/api/v1/chama/payments", other_pair).json()["payments"] == []
