"""Chama setup over HTTP: member codes, constitution rules, invitations, roster import, roles."""

from typing import Any

import pytest
from django.test import Client

from apps.accounts import sessions
from apps.accounts.models import User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.notifications import fake
from apps.notifications.invites import invite_outbox
from apps.organizations.codes import is_valid_member_code, new_member_code, normalize_member_code
from apps.organizations.models import Constitution, Membership
from core.tenancy import chama_context

pytestmark = pytest.mark.django_db

CHAIR_PHONE = "+254712000101"
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


def _login(phone: str, installation: str = "install-setup-1") -> TokenPair:
    user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
    return sessions.login(user.id, DeviceInfo(installation, "android"), None)


def _fresh_otp(client: Client, pair: TokenPair, phone: str) -> None:
    sent = _call(client, "post", "/api/v1/auth/step-up/otp/request", pair)
    assert sent.status_code == 202, sent.content
    code = fake.outbox(phone)[0]["code"]
    verified = _call(
        client,
        "post",
        "/api/v1/auth/step-up/otp/verify",
        pair,
        {"challenge_id": sent.json()["challenge_id"], "code": code},
    )
    assert verified.status_code == 200, verified.content


def _set_pin(client: Client, pair: TokenPair, phone: str) -> None:
    _fresh_otp(client, pair, phone)
    assert _call(client, "put", "/api/v1/auth/pin", pair, {"pin": PIN}).status_code == 204


@pytest.fixture
def chair(client) -> dict[str, Any]:
    pair = _login(CHAIR_PHONE)
    created = _call(client, "post", "/api/v1/chamas", pair, {"name": "Umoja Savers"})
    assert created.status_code == 201, created.content
    return {"pair": pair, "chama_id": created.json()["chama_id"], "body": created.json()}


# ---------------------------------------------------------------- member codes


def test_member_codes_are_valid_and_typos_are_refused():
    for _ in range(50):
        code = new_member_code()
        assert is_valid_member_code(code)
        for i, ch in enumerate(code):
            for other in "23456789ABCDEFGHJKLMNPQRSTUVWXYZ":
                if other != ch:
                    assert not is_valid_member_code(code[:i] + other + code[i + 1 :])
    assert not is_valid_member_code("O0I1AB")  # look-alikes are not in the alphabet
    assert normalize_member_code(" ab-c.d_e ") == "ABCDE"


def test_creating_a_chama_gives_the_chair_a_code_and_default_rules(chair):
    assert is_valid_member_code(chair["body"]["member_code"])
    with chama_context(chair["chama_id"]):
        rules = Constitution.objects.get(chama_id=chair["chama_id"])
    assert (rules.contribution_frequency, rules.interest_method, rules.payment_channel) == (
        "monthly",
        "reducing",
        "paybill",
    )


# ---------------------------------------------------------------- constitution


def test_every_member_reads_the_rules_and_editing_needs_a_fresh_pin(client, chair):
    pair = chair["pair"]
    assert _call(client, "get", "/api/v1/chama/constitution", pair).json()["version"] == 1
    refused = _call(client, "patch", "/api/v1/chama/constitution", pair, {"grace_days": 5})
    assert refused.json()["error"]["code"] == "pin_not_set"
    _set_pin(client, pair, CHAIR_PHONE)
    changed = _call(
        client,
        "patch",
        "/api/v1/chama/constitution",
        pair,
        {
            "contribution_amount_minor": 500_000,
            "penalty_type": "flat",
            "penalty_flat_minor": 20_000,
            "loan_multiplier": "3.0",
        },
    )
    assert changed.status_code == 200, changed.content
    body = changed.json()
    assert (body["contribution_amount_minor"], body["penalty_flat_minor"], body["version"]) == (
        500_000,
        20_000,
        2,
    )


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"due_day": 29}, "due_day"),
        ({"contribution_frequency": "weekly", "due_day": 7}, "due_day"),
        ({"penalty_type": "flat", "penalty_flat_minor": 0}, "penalty_flat_minor"),
        ({"penalty_type": "percent", "penalty_rate_bp": 0}, "penalty_rate_bp"),
        ({"grace_days": 31}, "grace_days"),
        ({"loan_multiplier": "21.0"}, "loan_multiplier"),
        ({"max_loan_months": 0}, "max_loan_months"),
        ({"payment_channel": "bank"}, "payment_channel"),
    ],
)
def test_invalid_rules_are_refused_and_nothing_changes(client, chair, change, field):
    pair = chair["pair"]
    _set_pin(client, pair, CHAIR_PHONE)
    response = _call(client, "patch", "/api/v1/chama/constitution", pair, change)
    assert response.status_code == 400, response.content
    assert field in response.json()["error"]["field_errors"]
    with chama_context(chair["chama_id"]):
        assert Constitution.objects.get(chama_id=chair["chama_id"]).version == 1


# ---------------------------------------------------------------- invitations


def test_invite_accept_and_member_view(client, chair, django_capture_on_commit_callbacks):
    pair = chair["pair"]
    body = {"phone": "0712000202", "name": "Wanjiru"}
    assert _call(client, "post", "/api/v1/chama/invitations", pair, body).status_code == 403
    _fresh_otp(client, pair, CHAIR_PHONE)
    with django_capture_on_commit_callbacks(execute=True):
        invited = _call(client, "post", "/api/v1/chama/invitations", pair, body)
    assert invited.status_code == 201, invited.content
    code = invited.json()["member_code"]
    assert invite_outbox("+254712000202")[0] == {
        "chama": "Umoja Savers",
        "member_code": code,
        "sent_at": invite_outbox("+254712000202")[0]["sent_at"],
    }

    member = _login("+254712000202", "install-setup-2")
    me = _call(client, "get", "/api/v1/me", member).json()
    assert me["memberships"][0]["status"] == "invited"
    url = f"/api/v1/invitations/{chair['chama_id']}/accept"
    assert _call(client, "post", url, member, {"accept_terms": False}).status_code == 400
    accepted = _call(client, "post", url, member, {"accept_terms": True})
    assert accepted.json() == {"chama_id": chair["chama_id"], "role": "member", "member_code": code}
    with chama_context(chair["chama_id"]):
        row = Membership.objects.get(member_code=code)
    assert (row.status, row.scheduled_stk_opt_in) == ("active", True)
    assert row.terms_version == "2026-11-25"

    switch = {"chama_id": chair["chama_id"]}
    _call(client, "post", "/api/v1/auth/switch-chama", member, switch)
    listed = _call(client, "get", "/api/v1/chama/members", member).json()["members"]
    assert {m["phone"] for m in listed} == {None}  # members never see phone numbers
    assert _call(client, "get", "/api/v1/chama/security-events", member).status_code == 403


def test_only_the_chair_invites_officials(client, chair):
    pair = chair["pair"]
    _fresh_otp(client, pair, CHAIR_PHONE)
    body = {"phone": "0712000203", "name": "Otieno", "role": "treasurer"}
    response = _call(client, "post", "/api/v1/chama/invitations", pair, body)
    assert response.json()["role"] == "treasurer"
    treasurer = _login("+254712000203", "install-setup-3")
    url = f"/api/v1/invitations/{chair['chama_id']}/accept"
    _call(client, "post", url, treasurer, {"accept_terms": True})
    _call(client, "post", "/api/v1/auth/switch-chama", treasurer, {"chama_id": chair["chama_id"]})
    _fresh_otp(client, treasurer, "+254712000203")
    official = {"phone": "0712000204", "name": "Achieng", "role": "secretary"}
    refused = _call(client, "post", "/api/v1/chama/invitations", treasurer, official)
    assert refused.status_code == 403
    plain = {"phone": "0712000204", "name": "Achieng"}
    assert _call(client, "post", "/api/v1/chama/invitations", treasurer, plain).status_code == 201


# ---------------------------------------------------------------- roster import

ROSTER = (
    "Name,Phone,Role\n"
    "Akinyi,0712000301,\n"
    "Baraka,+254712000302,member\n"
    ",0712000303,\n"
    "Chebet,12345,\n"
    "Duncan,0712000301,\n"
    "Esther,0712000305,boss\n"
    "Chair again,0712000101,\n"
)


def test_roster_preview_writes_nothing_and_commit_invites_valid_rows(
    client, chair, django_capture_on_commit_callbacks
):
    pair = chair["pair"]
    preview = _call(client, "post", "/api/v1/chama/members/import", pair, {"csv": ROSTER})
    assert preview.status_code == 200, preview.content
    body = preview.json()
    assert [r["error"] for r in body["rows"]] == [
        None,
        None,
        "invalid_name",
        "invalid_phone",
        "duplicate_in_file",
        "invalid_role",
        "already_member",
    ]
    assert (body["valid"], body["invalid"], body["invited"]) == (2, 5, 0)
    with chama_context(chair["chama_id"]):
        assert Membership.objects.count() == 1

    commit = {"csv": ROSTER, "commit": True}
    refused = _call(client, "post", "/api/v1/chama/members/import", pair, commit)
    assert refused.json()["error"]["code"] == "step_up_required"
    _fresh_otp(client, pair, CHAIR_PHONE)
    with django_capture_on_commit_callbacks(execute=True):
        done = _call(client, "post", "/api/v1/chama/members/import", pair, commit).json()
    assert done["invited"] == 2
    with chama_context(chair["chama_id"]):
        invited = Membership.objects.filter(status="invited")
        assert sorted(invited.values_list("roster_name", flat=True)) == ["Akinyi", "Baraka"]
        codes = list(Membership.objects.values_list("member_code", flat=True))
    assert len(set(codes)) == 3
    assert invite_outbox("+254712000301") and invite_outbox("+254712000302")


@pytest.mark.parametrize(
    ("csv", "reason"), [("phone\n0712000301\n", "missing_columns"), ("name,phone\n", "empty")]
)
def test_roster_shape_errors(client, chair, csv, reason):
    response = _call(client, "post", "/api/v1/chama/members/import", chair["pair"], {"csv": csv})
    assert response.json()["error"]["field_errors"]["csv"] == [reason]


# ---------------------------------------------------------------- roles


def test_the_last_chair_cannot_be_demoted(client, chair):
    pair = chair["pair"]
    _fresh_otp(client, pair, CHAIR_PHONE)
    with chama_context(chair["chama_id"]):
        own = Membership.objects.get().id
    response = _call(client, "patch", f"/api/v1/chama/members/{own}", pair, {"role": "member"})
    assert response.json()["error"]["field_errors"]["role"] == ["last_chair"]
