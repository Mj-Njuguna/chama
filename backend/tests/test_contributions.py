"""Contributions: cycle generation, payment allocation, late fines, two-official waivers,
reminders and scheduled prompts, the HTTP API and isolation."""

import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.contributions import tasks
from apps.contributions.cycles import ensure_cycles, periods_around, today
from apps.contributions.fines import approve_waiver, assess_fines, fine_amount, request_waiver
from apps.contributions.models import (
    Allocation,
    Cycle,
    FineWaiver,
    Obligation,
    ObligationKind,
    ObligationStatus,
)
from apps.contributions.services import allocate
from apps.ledger.models import LedgerTransaction
from apps.ledger.services import LedgerAccount, account_balance, member_balance
from apps.notifications import fake
from apps.notifications.models import FeedEvent, Message
from apps.organizations.models import Chama, Constitution, Membership
from core.api_errors import ApiError
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db
D = date.fromisoformat


@pytest.fixture(autouse=True)
def _fakes(fake_senders):
    fake.MESSAGE_BEHAVIOUR.clear()
    yield fake_senders
    fake.MESSAGE_BEHAVIOUR.clear()


def _user(phone: str) -> User:
    return User.objects.filter(phone=phone).first() or User.objects.create_user(phone)


class World:
    """One chama with a chair, a treasurer and two members, monthly KES 1,000 due on the 5th."""

    def __init__(self, phone_prefix: str = "+2547120003", **rules: Any) -> None:
        self.chama = Chama.objects.create(name="Umoja")
        self.phones: dict[str, str] = {}
        with chama_context(self.chama.id):
            Constitution.objects.create(
                chama=self.chama,
                contribution_frequency=rules.get("frequency", "monthly"),
                contribution_amount_minor=rules.get("amount", 100_000),
                due_day=rules.get("due_day", 5),
                grace_days=rules.get("grace_days", 3),
                penalty_type=rules.get("penalty_type", "flat"),
                penalty_flat_minor=rules.get("penalty_flat_minor", 20_000),
                penalty_rate_bp=rules.get("penalty_rate_bp", 0),
            )
            self.chair = self._member("chair", f"{phone_prefix}01")
            self.treasurer = self._member("treasurer", f"{phone_prefix}02")
            self.alice = self._member("member", f"{phone_prefix}03", opt_in=True)
            self.bob = self._member("member", f"{phone_prefix}04", consented=False)

    def _member(
        self, role: str, phone: str, *, opt_in: bool = False, consented: bool = True
    ) -> uuid.UUID:
        member = Membership.objects.create(
            chama=self.chama,
            user=_user(phone),
            role=role,
            joined_at=timezone.now(),
            terms_version="v1" if consented else "",
            terms_accepted_at=timezone.now() if consented else None,
            scheduled_stk_opt_in=opt_in,
        )
        self.phones[str(member.id)] = phone
        return member.id

    def ctx(self) -> Any:
        return chama_context(self.chama.id)


@pytest.fixture
def world() -> World:
    return World()


def _contribution(w: World, member: uuid.UUID, due: date, amount: int = 100_000) -> Obligation:
    cycle, _ = Cycle.objects.get_or_create(
        chama_id=w.chama.id,
        period_start=due.replace(day=1),
        defaults={
            "frequency": "monthly",
            "due_date": due,
            "grace_until": due + timedelta(days=3),
            "amount_minor": amount,
        },
    )
    return Obligation.objects.create(
        chama_id=w.chama.id,
        member_id=member,
        kind=ObligationKind.CONTRIBUTION,
        cycle_id=cycle.id,
        due_date=cycle.due_date,
        grace_until=cycle.grace_until,
        amount_minor=amount,
    )


# ---------------------------------------------------------------- cycles


def test_periods_monthly_and_weekly():
    assert [p.due_date for p in periods_around(D("2026-12-20"), "monthly", 5)] == [
        D("2026-12-05"),
        D("2027-01-05"),
    ]
    weekly = periods_around(D("2026-12-02"), "weekly", 4)  # a Wednesday; due Friday
    assert [p.start for p in weekly] == [D("2026-11-30"), D("2026-12-07")]
    assert [p.due_date for p in weekly] == [D("2026-12-04"), D("2026-12-11")]


def test_cycle_generation_is_idempotent_and_covers_active_members(world):
    with world.ctx():
        made = ensure_cycles(world.chama.id, D("2026-11-30"))  # Dec 5 is 5 days away
        assert made.cycles == 1
        assert made.obligations == 4
        again = ensure_cycles(world.chama.id, D("2026-12-01"))
        assert (again.cycles, again.obligations) == (0, 0)
        cycle = Cycle.objects.get()
        assert (cycle.due_date, cycle.grace_until) == (D("2026-12-05"), D("2026-12-08"))
        assert FeedEvent.objects.filter(kind="cycle_opened").count() == 1


def test_no_cycle_before_the_lead_window_or_with_zero_amount(world):
    with world.ctx():
        assert ensure_cycles(world.chama.id, D("2026-11-20")).cycles == 0
        Constitution.objects.filter(chama=world.chama).update(contribution_amount_minor=0)
        assert ensure_cycles(world.chama.id, D("2026-12-01")).cycles == 0


# ---------------------------------------------------------------- allocation


def test_allocation_order_fines_then_contributions_then_savings(world):
    with world.ctx():
        ob = _contribution(world, world.alice, D("2026-11-05"))
        assert assess_fines(world.chama.id, D("2026-11-09"))[0].amount_minor == 20_000
        result = allocate(
            world.chama.id,
            member_id=world.alice,
            amount_minor=150_000,
            source_key="payment:p1",
            debit_account="cash_mpesa",
        )
        assert (result.fines_minor, result.loans_minor) == (20_000, 0)
        assert (result.contributions_minor, result.savings_minor) == (100_000, 30_000)
        ob.refresh_from_db()
        assert ob.status == ObligationStatus.PAID
        assert member_balance(LedgerAccount.FINES_RECEIVABLE, world.alice) == 0
        assert member_balance(LedgerAccount.MEMBER_SAVINGS, world.alice) == -130_000
        assert account_balance(LedgerAccount.CASH_MPESA) == 150_000


def test_allocation_is_idempotent(world):
    with world.ctx():
        _contribution(world, world.alice, D("2026-12-05"))
        kwargs = {
            "member_id": world.alice,
            "amount_minor": 40_000,
            "source_key": "payment:p2",
            "debit_account": "cash_mpesa",
        }
        first = allocate(world.chama.id, **kwargs)
        second = allocate(world.chama.id, **kwargs)
        assert first == second
        assert Allocation.objects.count() == 1
        assert LedgerTransaction.objects.count() == 1
        assert Obligation.objects.get().paid_minor == 40_000


def test_partial_payment_spreads_oldest_first(world):
    with world.ctx():
        older = _contribution(world, world.alice, D("2026-11-05"))
        newer = _contribution(world, world.alice, D("2026-12-05"))
        allocate(
            world.chama.id,
            member_id=world.alice,
            amount_minor=150_000,
            source_key="receipt:r1",
            debit_account="cash_bank",
        )
        older.refresh_from_db()
        newer.refresh_from_db()
        assert (older.status, older.paid_minor) == (ObligationStatus.PAID, 100_000)
        assert (newer.status, newer.paid_minor) == (ObligationStatus.OPEN, 50_000)


def test_due_installments_are_paid_before_contributions(world, monkeypatch):
    calls: list[tuple[int, int]] = []
    item = SimpleNamespace(
        installment_id=uuid.uuid4(),
        loan_id=uuid.uuid4(),
        due_date=D("2026-11-30"),
        interest_due_minor=5_000,
        principal_due_minor=45_000,
    )
    monkeypatch.setattr("apps.lending.services.due_installments", lambda member: [item])
    monkeypatch.setattr(
        "apps.lending.services.record_installment_payment",
        lambda _id, *, interest_minor, principal_minor, source_key: calls.append(
            (interest_minor, principal_minor)
        ),
    )
    with world.ctx():
        _contribution(world, world.alice, D("2026-12-05"))
        result = allocate(
            world.chama.id,
            member_id=world.alice,
            amount_minor=60_000,
            source_key="payment:p3",
            debit_account="cash_mpesa",
        )
        assert (result.loans_minor, result.contributions_minor) == (50_000, 10_000)
        assert calls == [(5_000, 45_000)]
        assert member_balance(LedgerAccount.INTEREST_RECEIVABLE, world.alice) == -5_000
        assert member_balance(LedgerAccount.LOANS_RECEIVABLE, world.alice) == -45_000


def test_allocation_refuses_bad_input(world):
    with world.ctx():
        for amount, account in ((0, "cash_mpesa"), (100, "member_savings")):
            with pytest.raises(ValueError):
                allocate(
                    world.chama.id,
                    member_id=world.alice,
                    amount_minor=amount,
                    source_key="x",
                    debit_account=account,
                )


# ---------------------------------------------------------------- fines


def test_fine_only_after_grace_and_only_once(world):
    with world.ctx():
        _contribution(world, world.alice, D("2026-11-05"))  # grace until Nov 8
        assert assess_fines(world.chama.id, D("2026-11-08")) == []  # last grace day
        issued = assess_fines(world.chama.id, D("2026-11-09"))
        assert len(issued) == 1
        assert assess_fines(world.chama.id, D("2026-11-10")) == []
        assert Obligation.objects.filter(kind=ObligationKind.FINE).count() == 1
        assert member_balance(LedgerAccount.FINES_RECEIVABLE, world.alice) == 20_000
        assert account_balance(LedgerAccount.FINE_INCOME) == -20_000


def test_paid_contributions_are_not_fined(world):
    with world.ctx():
        _contribution(world, world.alice, D("2026-11-05"))
        allocate(
            world.chama.id,
            member_id=world.alice,
            amount_minor=100_000,
            source_key="payment:p4",
            debit_account="cash_mpesa",
        )
        assert assess_fines(world.chama.id, D("2026-11-20")) == []


def test_percent_fine_rounds_half_up():
    rules = {"penalty_type": "percent", "penalty_rate_bp": 500, "penalty_flat_minor": 0}
    assert fine_amount(rules, 1_030) == 52  # 51.5 -> 52
    assert fine_amount(rules, 1_010) == 51  # 50.5 -> 51
    assert fine_amount({"penalty_type": "none"}, 1_000) == 0


def test_one_fine_per_contribution_in_the_database(world):
    with world.ctx():
        ob = _contribution(world, world.alice, D("2026-11-05"))
        assess_fines(world.chama.id, D("2026-11-09"))
        with pytest.raises(IntegrityError), transaction.atomic():
            Obligation.objects.create(
                chama_id=world.chama.id,
                member_id=world.alice,
                kind=ObligationKind.FINE,
                fined_obligation_id=ob.id,
                due_date=D("2026-11-09"),
                grace_until=D("2026-11-09"),
                amount_minor=1,
            )


# ---------------------------------------------------------------- waivers


def _fine(world: World, member: uuid.UUID) -> Obligation:
    _contribution(world, member, D("2026-11-05"))
    assess_fines(world.chama.id, D("2026-11-09"))
    return Obligation.objects.get(kind=ObligationKind.FINE, member_id=member)


def test_waiver_needs_a_second_official(world):
    with world.ctx():
        fine = _fine(world, world.alice)
        waiver = request_waiver(world.chama.id, fine.id, world.treasurer, "bereavement")
        assert waiver.amount_minor == 20_000
        with pytest.raises(ApiError) as same:
            approve_waiver(world.chama.id, waiver.waiver_id, world.treasurer)
        assert same.value.extra["reason"] == "same_official"
        done = approve_waiver(world.chama.id, waiver.waiver_id, world.chair)
        assert done.status == "approved"
        fine.refresh_from_db()
        assert fine.status == ObligationStatus.WAIVED
        assert member_balance(LedgerAccount.FINES_RECEIVABLE, world.alice) == 0
        assert account_balance(LedgerAccount.FINE_INCOME) == 0
        assert FeedEvent.objects.filter(kind="fine_waived").count() == 1
        with pytest.raises(ApiError):
            approve_waiver(world.chama.id, waiver.waiver_id, world.chair)


def test_the_fined_official_cannot_approve_their_own_waiver(world):
    with world.ctx():
        fine = _fine(world, world.chair)
        waiver = request_waiver(world.chama.id, fine.id, world.treasurer, "error")
        with pytest.raises(ApiError):
            approve_waiver(world.chama.id, waiver.waiver_id, world.chair)


def test_the_database_refuses_a_one_person_waiver(world):
    with world.ctx():
        fine = _fine(world, world.alice)
        waiver = request_waiver(world.chama.id, fine.id, world.treasurer, "x")
        with pytest.raises(IntegrityError), transaction.atomic():
            FineWaiver.objects.filter(id=waiver.waiver_id).update(
                approved_by_member_id=world.treasurer
            )


# ---------------------------------------------------------------- reminders and prompts


def test_reminders_are_consent_gated_and_idempotent(world, django_capture_on_commit_callbacks):
    due = today()
    with world.ctx():
        for member in (world.alice, world.bob):
            _contribution(world, member, due)
    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.due_reminders(str(world.chama.id)) == 0  # no payments STK yet
        tasks.due_reminders(str(world.chama.id))
    with world.ctx():
        by_member = {m.member_id: m for m in Message.objects.all()}
    assert len(by_member) == 2
    assert by_member[world.alice].status == "sent"
    assert by_member[world.bob].status == "skipped"  # never accepted the terms
    sent = fake.message_outbox(world.phones[str(world.alice)])
    assert [m["template"] for m in sent] == ["contribution_reminder"]


def test_scheduled_prompts_only_for_opted_in_members_with_a_balance(world, monkeypatch):
    requested: list[dict[str, Any]] = []

    def fake_stk(chama_id, **kwargs):
        requested.append(kwargs)

    monkeypatch.setattr("apps.payments.services.request_stk_push", fake_stk, raising=False)
    due = today()
    with world.ctx():
        for member in (world.alice, world.bob, world.chair):
            _contribution(world, member, due)
    tasks.due_reminders(str(world.chama.id))
    tasks.due_reminders(str(world.chama.id))
    assert {r["member_id"] for r in requested} == {world.alice}
    keys = {r["idempotency_key"] for r in requested}
    assert len(keys) == 1 and next(iter(keys)).endswith(":stk")
    assert requested[0]["reason"] == "scheduled"
    assert requested[0]["amount_minor"] == 100_000


def test_pre_reminders_target_three_days_ahead(world, django_capture_on_commit_callbacks):
    with world.ctx():
        _contribution(world, world.alice, today() + timedelta(days=3))
    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.pre_reminders(str(world.chama.id)) == 1


# ---------------------------------------------------------------- HTTP


def _user_of(world: World, member: uuid.UUID) -> User:
    return _user(world.phones[str(member)])


def _login(user: User, chama: Chama) -> TokenPair:
    return sessions.login(
        user.id, DeviceInfo(f"install-{uuid.uuid4().hex[:8]}", "android"), chama.id
    )


def _get(client: Client, url: str, pair: TokenPair) -> Any:
    return client.get(url, headers={"Authorization": f"Bearer {pair.access_token}"})


def test_members_see_their_own_obligations_officials_see_all(client, world):
    with world.ctx():
        _contribution(world, world.alice, today())
    alice = _login(_user_of(world, world.alice), world.chama)
    chair = _login(_user_of(world, world.chair), world.chama)
    mine = _get(client, "/api/v1/chama/obligations", alice)
    assert mine.status_code == 200
    assert len(mine.json()["obligations"]) == 1
    other = _get(client, f"/api/v1/chama/obligations?member={world.bob}", alice)
    assert other.status_code == 403
    as_chair = _get(client, f"/api/v1/chama/obligations?member={world.alice}", chair)
    assert len(as_chair.json()["obligations"]) == 1
    assert _get(client, "/api/v1/chama/collection", alice).status_code == 403
    collection = _get(client, "/api/v1/chama/collection", chair).json()
    assert collection["members"][0]["status"] == "unpaid"
    home = _get(client, "/api/v1/chama/home", chair).json()
    assert home["official"]["collection_rate_percent"] == 0
    assert home["official"]["pending_approvals"] == 0
    member_home = _get(client, "/api/v1/chama/home", alice).json()
    assert member_home["member"]["cycle_status"] == "due"
    assert member_home["member"]["cycle_balance_minor"] == 100_000


def test_membership_preferences(client, world):
    bob = _login(_user_of(world, world.bob), world.chama)
    url = "/api/v1/chama/membership"
    headers = {"Authorization": f"Bearer {bob.access_token}"}
    assert client.get(url, headers=headers).json() == {"scheduled_stk_opt_in": False}
    changed = client.patch(
        url, {"scheduled_stk_opt_in": True}, content_type="application/json", headers=headers
    )
    assert changed.json() == {"scheduled_stk_opt_in": True}
    assert client.get(url, headers=headers).json() == {"scheduled_stk_opt_in": True}


def test_statement_and_cashbook(client, world):
    with world.ctx():
        _contribution(world, world.alice, today())
        allocate(
            world.chama.id,
            member_id=world.alice,
            amount_minor=120_000,
            source_key="payment:s1",
            debit_account="cash_mpesa",
        )
    alice = _login(_user_of(world, world.alice), world.chama)
    chair = _login(_user_of(world, world.chair), world.chama)
    statement = _get(client, "/api/v1/chama/statement", alice).json()
    assert [(e["account"], e["amount_minor"]) for e in statement["entries"]] == [
        ("member_savings", 120_000)
    ]
    assert _get(client, "/api/v1/chama/cashbook", alice).status_code == 403
    cashbook = _get(client, "/api/v1/chama/cashbook", chair).json()
    assert cashbook["balance_minor"] == 120_000
    assert cashbook["transactions"][0]["cash_minor"] == 120_000


def test_another_chama_sees_none_of_these_rows(client, world):
    with world.ctx():
        _contribution(world, world.alice, today())
        assess_fines(world.chama.id, today() + timedelta(days=10))
    other = World(phone_prefix="+2547120004")
    other_chair = _login(_user_of(other, other.chair), other.chama)
    assert _get(client, "/api/v1/chama/collection", other_chair).json()["members"] == []
    assert _get(client, "/api/v1/chama/feed", other_chair).json()["events"] == []
    leaked = _get(client, f"/api/v1/chama/obligations?member={world.alice}", other_chair)
    assert leaked.status_code == 404
    with other.ctx():
        assert Obligation.objects.count() == 0
        assert Cycle.objects.count() == 0
