"""The ledger: balance at commit, append-only, idempotent postings, member balances, manual
receipts over HTTP, the home read model and cross-chama isolation."""

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.test import Client

from apps.accounts import sessions
from apps.accounts.models import User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.ledger.models import LedgerEntry, LedgerTransaction, Receipt
from apps.ledger.services import (
    LedgerAccount,
    Line,
    UnbalancedPosting,
    balances_by_member,
    cash_balance,
    member_balance,
    post,
    reverse,
)
from apps.notifications import fake
from apps.organizations.models import Membership
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

CHAIR_PHONE = "+254712000201"
TODAY = datetime.now(ZoneInfo("Africa/Nairobi")).date()
MEMBER_PHONE = "+254712000202"
PIN = "275931"


@pytest.fixture(autouse=True)
def _fakes(fake_senders):
    return fake_senders


def _h(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


def _call(client: Client, method: str, url: str, pair: TokenPair, body: Any = None) -> Any:
    return getattr(client, method)(
        url, body or {}, content_type="application/json", headers=_h(pair)
    )


def _login(phone: str, installation: str) -> TokenPair:
    user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
    return sessions.login(user.id, DeviceInfo(installation, "android"), None)


def _fresh_otp(client: Client, pair: TokenPair, phone: str) -> None:
    sent = _call(client, "post", "/api/v1/auth/step-up/otp/request", pair)
    assert sent.status_code == 202, sent.content
    code = fake.outbox(phone)[-1]["code"]
    body = {"challenge_id": sent.json()["challenge_id"], "code": code}
    verified = _call(client, "post", "/api/v1/auth/step-up/otp/verify", pair, body)
    assert verified.status_code == 200, verified.content


def _fresh_pin(client: Client, pair: TokenPair, phone: str) -> None:
    _fresh_otp(client, pair, phone)
    assert _call(client, "put", "/api/v1/auth/pin", pair, {"pin": PIN}).status_code == 204
    ok = _call(client, "post", "/api/v1/auth/step-up/pin", pair, {"pin": PIN})
    assert ok.status_code in (200, 204), ok.content


def _member(chama_id: uuid.UUID, phone: str, role: str = "member") -> uuid.UUID:
    user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
    with chama_context(chama_id):
        return Membership.objects.create(chama_id=chama_id, user=user, role=role).id


@pytest.fixture
def world(client) -> dict[str, Any]:
    pair = _login(CHAIR_PHONE, "install-ledger-chair")
    created = _call(client, "post", "/api/v1/chamas", pair, {"name": "Umoja Savers"})
    assert created.status_code == 201, created.content
    chama_id = uuid.UUID(created.json()["chama_id"])
    member_id = _member(chama_id, MEMBER_PHONE)
    return {"pair": pair, "chama_id": chama_id, "member_id": member_id}


def _sql(chama_id: uuid.UUID, statement: str, params: list[Any] | None = None) -> None:
    with chama_context(chama_id), connection.cursor() as cursor:
        cursor.execute(statement, params or [])


# ---------------------------------------------------------------- integrity in the database


def test_an_unbalanced_transaction_is_rejected_at_commit(world):
    chama, txn = world["chama_id"], uuid.uuid4()
    with pytest.raises(IntegrityError, match="does not balance"), transaction.atomic():
        _sql(
            chama,
            "INSERT INTO ledger_ledgertransaction (id, chama_id, kind, key, memo, created_at)"
            " VALUES (%s, %s, 'receipt', 'test:unbalanced', '', now())",
            [txn, chama],
        )
        _sql(
            chama,
            "INSERT INTO ledger_ledgerentry (id, chama_id, transaction_id, ledger_account,"
            " amount_minor, created_at) VALUES (%s, %s, %s, 'cash_bank', 100, now()),"
            " (%s, %s, %s, 'unallocated', -99, now())",
            [uuid.uuid4(), chama, txn, uuid.uuid4(), chama, txn],
        )
        _sql(chama, "SET CONSTRAINTS ALL IMMEDIATE")


def test_a_transaction_needs_two_entries(world):
    chama = world["chama_id"]
    with pytest.raises(IntegrityError, match="does not balance"), transaction.atomic():
        _sql(
            chama,
            "INSERT INTO ledger_ledgertransaction (id, chama_id, kind, key, memo, created_at)"
            " VALUES (%s, %s, 'receipt', 'test:empty', '', now())",
            [uuid.uuid4(), chama],
        )
        _sql(chama, "SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_ledgerentry SET amount_minor = amount_minor",
        "DELETE FROM ledger_ledgerentry",
        "UPDATE ledger_ledgertransaction SET key = key",
        "DELETE FROM ledger_ledgertransaction",
        "UPDATE ledger_receipt SET amount_minor = amount_minor",
        "DELETE FROM ledger_receipt",
    ],
)
def test_the_ledger_is_append_only(world, statement):
    with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
        _sql(world["chama_id"], statement)


def test_a_per_member_entry_must_name_a_member(world):
    chama = world["chama_id"]
    with chama_context(chama), pytest.raises(UnbalancedPosting):
        post(
            chama,
            "receipt",
            "test:nomember",
            [Line("cash_bank", 100), Line("member_savings", -100)],
        )


def test_an_entry_cannot_name_another_chamas_member(world, client):
    other = _login("+254712000299", "install-ledger-other")
    created = _call(client, "post", "/api/v1/chamas", other, {"name": "Other"})
    other_chama = uuid.UUID(created.json()["chama_id"])
    foreign_member = _member(other_chama, "+254712000298")
    chama = world["chama_id"]
    with pytest.raises(IntegrityError), transaction.atomic(), chama_context(chama):
        post(
            chama,
            "receipt",
            "test:foreign",
            [Line("cash_bank", 100), Line("member_savings", -100, foreign_member)],
        )


# ---------------------------------------------------------------- postings and balances


def test_posting_is_idempotent_and_balances_add_up(world):
    chama, member = world["chama_id"], world["member_id"]
    lines = [Line("cash_mpesa", 5_000_00), Line("member_savings", -5_000_00, member)]
    with chama_context(chama):
        assert post(chama, "payment", "payment:ABC123", lines, member_id=member) is True
        assert post(chama, "payment", "payment:ABC123", lines, member_id=member) is False
        assert LedgerTransaction.objects.count() == 1
        assert cash_balance() == 5_000_00
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, member) == -5_000_00
        assert balances_by_member(LedgerAccount.MEMBER_SAVINGS) == {member: -5_000_00}


def test_service_refuses_unbalanced_lines(world):
    chama = world["chama_id"]
    with chama_context(chama):
        for lines in ([Line("cash_bank", 100)], [Line("cash_bank", 100), Line("expenses", -1)]):
            with pytest.raises(UnbalancedPosting):
                post(chama, "expense", "test:bad", lines)


def test_reversal_posts_the_exact_opposite(world):
    chama, member = world["chama_id"], world["member_id"]
    with chama_context(chama):
        post(
            chama,
            "receipt",
            "receipt:1",
            [Line("cash_bank", 700), Line("member_savings", -700, member)],
        )
        assert reverse(chama, "receipt:1", "receipt:1:reversed") is True
        assert reverse(chama, "receipt:1", "receipt:1:reversed") is False
        assert cash_balance() == 0
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, member) == 0
        with pytest.raises(LookupError):
            reverse(chama, "receipt:missing", "receipt:missing:reversed")


# ---------------------------------------------------------------- manual receipts over HTTP


def _receipt(client, world, **overrides) -> Any:
    body = {
        "channel": "bank",
        "reference": "ftc 2611 0042",
        "amount_minor": 5_000_00,
        "received_on": TODAY.isoformat(),
        "member_id": str(world["member_id"]),
        **overrides,
    }
    return _call(client, "post", "/api/v1/chama/receipts", world["pair"], body)


def test_recording_a_receipt_needs_a_fresh_pin(client, world):
    refused = _receipt(client, world)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] in ("step_up_required", "pin_not_set")


def test_receipt_credits_the_member_and_shows_on_home(client, world):
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    made = _receipt(client, world)
    assert made.status_code == 201, made.content
    assert made.json()["reference"] == "FTC26110042"

    home = _call(client, "get", "/api/v1/chama/home", world["pair"]).json()
    assert home["role"] == "chair"
    assert home["official"]["liquidity_minor"] == 5_000_00
    assert home["member"]["savings_minor"] == 0  # the chair's own savings

    member_pair = _login(MEMBER_PHONE, "install-ledger-member")
    assert (
        _call(
            client,
            "post",
            "/api/v1/auth/switch-chama",
            member_pair,
            {"chama_id": str(world["chama_id"])},
        ).status_code
        == 200
    )
    mine = _call(client, "get", "/api/v1/chama/home", member_pair).json()
    assert mine["role"] == "member"
    assert mine["member"]["savings_minor"] == 5_000_00
    assert mine["member"]["active_loan_balance_minor"] == 0
    assert mine["official"] is None


def test_duplicate_reference_is_a_conflict_and_posts_nothing_twice(client, world):
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    assert _receipt(client, world).status_code == 201
    again = _receipt(client, world, reference="FTC-2611-0042".replace("-", ""))
    assert again.status_code == 409
    assert again.json()["error"]["reason"] == "duplicate_receipt"
    with chama_context(world["chama_id"]):
        assert Receipt.objects.count() == 1
        assert LedgerEntry.objects.count() == 2


def test_receipt_without_member_lands_in_unallocated(client, world):
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    made = _receipt(client, world, member_id=None, channel="mpesa", reference="SKL4H2JQ9P")
    assert made.status_code == 201, made.content
    with chama_context(world["chama_id"]):
        from apps.ledger.services import account_balance

        assert account_balance(LedgerAccount.UNALLOCATED) == -5_000_00
        assert account_balance(LedgerAccount.CASH_MPESA) == 5_000_00


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"amount_minor": 0}, "amount_minor"),
        ({"reference": "x"}, "reference"),
        ({"received_on": (TODAY + timedelta(days=2)).isoformat()}, "received_on"),
        ({"channel": "cash"}, "channel"),
    ],
)
def test_invalid_receipts_are_refused(client, world, override, field):
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    refused = _receipt(client, world, **override)
    assert refused.status_code == 400, refused.content
    assert field in refused.json()["error"]["field_errors"]


def test_receipt_for_an_unknown_member_is_a_conflict(client, world):
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    refused = _receipt(client, world, member_id=str(uuid.uuid4()))
    assert refused.status_code == 409
    assert refused.json()["error"]["reason"] == "member_not_active"


def test_plain_members_cannot_record_or_list_receipts(client, world):
    member_pair = _login(MEMBER_PHONE, "install-ledger-member2")
    _call(
        client,
        "post",
        "/api/v1/auth/switch-chama",
        member_pair,
        {"chama_id": str(world["chama_id"])},
    )
    assert _call(client, "get", "/api/v1/chama/receipts", member_pair).status_code == 403
    body = {
        "channel": "bank",
        "reference": "ABC123",
        "amount_minor": 100,
        "received_on": "2026-11-01",
    }
    assert _call(client, "post", "/api/v1/chama/receipts", member_pair, body).status_code == 403


def test_another_chama_sees_no_ledger_rows(client, world):
    _fresh_pin(client, world["pair"], CHAIR_PHONE)
    assert _receipt(client, world).status_code == 201
    other = _login("+254712000297", "install-ledger-other2")
    _call(client, "post", "/api/v1/chamas", other, {"name": "Other"})
    listed = _call(client, "get", "/api/v1/chama/receipts", other).json()
    assert listed["receipts"] == []
    home = _call(client, "get", "/api/v1/chama/home", other).json()
    assert home["official"]["liquidity_minor"] == 0
