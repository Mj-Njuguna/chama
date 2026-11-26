"""Device list and revoke, push tokens, the push provider fake.

Over HTTP through the real middleware unless a service is the thing under test.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import AuthSession, PushToken, User
from apps.accounts.services import push_targets_for_user
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.organizations.models import Chama, Membership
from apps.organizations.roles import Role
from core.api_errors import ApiError
from core.tenancy import chama_context, user_context

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"
EXPO = "ExponentPushToken[dev-0001]"  # shape irrelevant: formats are not checked


def _login(user: User, installation: str, chama: Chama | None = None) -> TokenPair:
    return sessions.login(
        user.id,
        DeviceInfo(installation, "android", f"Phone {installation[-1]}"),
        chama.id if chama else None,
    )


def _h(pair: TokenPair) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair.access_token}"}


def _device_id(pair: TokenPair) -> uuid.UUID:
    return AuthSession.objects.values_list("device_id", flat=True).get(id=pair.session_id)


def _register(client: Client, pair: TokenPair, token: str = EXPO, kind: str = "expo") -> Any:
    return client.put(
        "/api/v1/auth/push-token",
        {"token_type": kind, "token": token},
        content_type="application/json",
        headers=_h(pair),
    )


def _revoke(client: Client, pair: TokenPair, device: uuid.UUID) -> int:
    return int(client.delete(f"/api/v1/auth/devices/{device}", headers=_h(pair)).status_code)


def _targets(user: User) -> list[str]:
    return sorted(t.token for t in push_targets_for_user(user.id))


@pytest.fixture
def events_spy_devices(monkeypatch):
    from apps.accounts import audit

    kinds: list[str] = []

    def spy(kind, **kwargs):
        kinds.append(str(kind))
        return audit.record_security_event(kind, **kwargs)

    monkeypatch.setattr("apps.accounts.devices.record_security_event", spy)
    return kinds


@pytest.fixture
def me() -> User:
    return User.objects.create_user(PHONE)


# ---------------------------------------------------------------- device list


def test_device_list_shows_the_callers_live_devices_and_marks_this_one(client, me):
    first = _login(me, "install-0001")
    second = _login(me, "install-0002")
    other = _login(User.objects.create_user("0712000999"), "install-0003")
    response = client.get("/api/v1/auth/devices", headers=_h(second))
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert {d["id"] for d in devices} == {str(_device_id(first)), str(_device_id(second))}
    assert str(_device_id(other)) not in {d["id"] for d in devices}
    assert [d["current"] for d in devices if d["id"] == str(_device_id(second))] == [True]
    assert set(devices[0]) == {"id", "name", "platform", "created_at", "last_seen_at", "current"}


def test_revoked_devices_are_not_listed(client, me):
    first, second = _login(me, "install-0001"), _login(me, "install-0002")
    assert _revoke(client, second, _device_id(first)) == 204
    devices = client.get("/api/v1/auth/devices", headers=_h(second)).json()["devices"]
    assert [d["id"] for d in devices] == [str(_device_id(second))]


@pytest.mark.parametrize("how", ["logout", "refresh_reused", "expired"])
def test_devices_without_a_live_session_are_not_listed(client, me, how):
    first, second = _login(me, "install-0001"), _login(me, "install-0002")
    if how == "logout":
        client.post("/api/v1/auth/logout", headers=_h(first))
    elif how == "refresh_reused":
        rotated = sessions.refresh(first.refresh_token)
        sessions.refresh(rotated.refresh_token)
        with pytest.raises(ApiError):
            sessions.refresh(first.refresh_token)
    else:
        AuthSession.objects.filter(id=first.session_id).update(
            absolute_expires_at=timezone.now() - timedelta(seconds=1)
        )
    devices = client.get("/api/v1/auth/devices", headers=_h(second)).json()["devices"]
    assert [d["id"] for d in devices] == [str(_device_id(second))]


def test_a_new_installation_id_is_a_new_device_with_no_leftovers(client, me):
    old = _login(me, "install-0001")
    _register(client, old, "token-old")
    sessions.refresh(sessions.refresh(old.refresh_token).refresh_token)
    with pytest.raises(ApiError):
        sessions.refresh(old.refresh_token)  # terminal: refresh_reused; the app wipes itself
    new = _login(me, "install-0002")  # next login with a new installation ID
    assert _device_id(new) != _device_id(old)
    devices = client.get("/api/v1/auth/devices", headers=_h(new)).json()["devices"]
    assert [(d["id"], d["current"]) for d in devices] == [(str(_device_id(new)), True)]
    assert _targets(me) == []


# ---------------------------------------------------------------- device revoke (AUTH-08)


def test_auth_08_revoked_device_loses_its_sessions_at_once(client, me, events_spy_devices):
    phone, tablet = _login(me, "install-0001"), _login(me, "install-0002")
    response = client.delete(f"/api/v1/auth/devices/{_device_id(tablet)}", headers=_h(phone))
    assert response.status_code == 204
    assert client.get("/api/v1/me", headers=_h(tablet)).json()["error"]["code"] == "session_revoked"
    refreshed = client.post(
        "/api/v1/auth/token/refresh",
        {"refresh_token": tablet.refresh_token},
        content_type="application/json",
    )
    assert refreshed.json()["error"]["code"] == "session_revoked"
    assert client.get("/api/v1/me", headers=_h(phone)).status_code == 200  # others unaffected
    session = AuthSession.objects.get(id=tablet.session_id)
    assert session.revoked_reason == "device_revoked"
    assert "device_revoked" in events_spy_devices


def test_revoking_the_current_device_ends_this_session(client, me):
    pair = _login(me, "install-0001")
    assert _revoke(client, pair, _device_id(pair)) == 204
    assert client.get("/api/v1/me", headers=_h(pair)).status_code == 401


def test_logging_in_again_on_a_revoked_installation_reactivates_the_device(client, me):
    pair = _login(me, "install-0001")
    client.delete(f"/api/v1/auth/devices/{_device_id(pair)}", headers=_h(pair))
    again = _login(me, "install-0001")  # after a fresh OTP
    assert _device_id(again) == _device_id(pair)
    assert client.get("/api/v1/me", headers=_h(again)).status_code == 200


@pytest.mark.parametrize("case", ["other_users_device", "unknown", "already_revoked"])
def test_device_not_found_is_one_answer(client, me, case):
    pair = _login(me, "install-0001")
    if case == "other_users_device":
        target = _device_id(_login(User.objects.create_user("0712000999"), "install-0009"))
    elif case == "unknown":
        target = uuid.uuid4()
    else:
        spare = _login(me, "install-0002")
        target = _device_id(spare)
        client.delete(f"/api/v1/auth/devices/{target}", headers=_h(pair))
    response = client.delete(f"/api/v1/auth/devices/{target}", headers=_h(pair))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_another_users_device_stays_untouched(client, me):
    pair = _login(me, "install-0001")
    victim = _login(User.objects.create_user("0712000999"), "install-0009")
    client.delete(f"/api/v1/auth/devices/{_device_id(victim)}", headers=_h(pair))
    assert client.get("/api/v1/me", headers=_h(victim)).status_code == 200


def test_device_endpoints_work_after_the_active_membership_is_revoked(client, me):
    chama = Chama.objects.create()
    with chama_context(chama.id):
        Membership.objects.create(chama=chama, user=me, role=Role.CHAIR)
    pair = _login(me, "install-0001", chama)
    spare = _login(me, "install-0002", chama)
    with chama_context(chama.id):
        Membership.objects.filter(user=me).update(status=Membership.Status.REVOKED)
    assert client.get("/api/v1/auth/devices", headers=_h(pair)).status_code == 200
    assert _register(client, pair).status_code == 204
    assert _revoke(client, pair, _device_id(spare)) == 204
    assert client.delete("/api/v1/auth/push-token", headers=_h(pair)).status_code == 204


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/v1/auth/devices"),
        ("delete", f"/api/v1/auth/devices/{uuid.uuid4()}"),
        ("put", "/api/v1/auth/push-token"),
        ("delete", "/api/v1/auth/push-token"),
    ],
)
def test_device_endpoints_need_a_session(client, method, url):
    response = getattr(client, method)(url, content_type="application/json")
    assert response.status_code == 401


# ---------------------------------------------------------------- push tokens (AUTH-15)


def test_registered_token_is_a_push_target(client, me):
    pair = _login(me, "install-0001")
    assert _register(client, pair).status_code == 204
    targets = push_targets_for_user(me.id)
    assert [(t.device_id, t.token_type, t.token) for t in targets] == [
        (_device_id(pair), "expo", EXPO)
    ]


def test_registering_again_replaces_the_devices_token(client, me):
    pair = _login(me, "install-0001")
    _register(client, pair, "token-old")
    _register(client, pair, "token-new")
    _register(client, pair, "token-new")  # the same again: no new row
    assert _targets(me) == ["token-new"]
    with user_context(me.id):
        assert PushToken.objects.count() == 2


def test_each_device_keeps_its_own_token(client, me):
    _register(client, _login(me, "install-0001"), "token-a")
    _register(client, _login(me, "install-0002"), "token-b")
    assert _targets(me) == ["token-a", "token-b"]


def _session_ending(client, me, how: str) -> tuple[TokenPair, TokenPair]:
    """Two devices with tokens; end the first device's session in the given way."""
    first, second = _login(me, "install-0001"), _login(me, "install-0002")
    _register(client, first, "token-first")
    _register(client, second, "token-second")
    if how == "logout":
        client.post("/api/v1/auth/logout", headers=_h(first))
    elif how == "device_revoked":
        client.delete(f"/api/v1/auth/devices/{_device_id(first)}", headers=_h(second))
    elif how == "token_deleted":
        client.delete("/api/v1/auth/push-token", headers=_h(first))
    elif how == "refresh_reused":
        sessions.refresh(first.refresh_token)
        sessions.refresh(sessions.refresh(first.refresh_token).refresh_token)
        with pytest.raises(ApiError):
            sessions.refresh(first.refresh_token)
    elif how == "expired":
        AuthSession.objects.filter(id=first.session_id).update(
            absolute_expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif how == "key_revoked":
        from django.conf import settings

        sessions.refresh(first.refresh_token)
        key = settings.AUTH_TOKEN_KEYS.split(",")[0].split(":")[0]
        sessions.revoke_sessions_derived_with(key, operator="test")
    return first, second


@pytest.mark.parametrize(
    "how",
    ["logout", "device_revoked", "token_deleted", "refresh_reused", "expired", "key_revoked"],
)
def test_auth_15_an_ended_session_or_device_never_yields_its_token(client, me, how):
    _session_ending(client, me, how)
    assert _targets(me) == ["token-second"]


def test_auth_15_explicit_paths_also_mark_the_row_revoked(client, me):
    first, second = _login(me, "install-0001"), _login(me, "install-0002")
    _register(client, first, "token-first")
    _register(client, second, "token-second")
    client.post("/api/v1/auth/logout", headers=_h(first))
    with user_context(me.id):
        revoked = dict(PushToken.objects.values_list("token", "revoked_at"))
    assert revoked["token-first"] is not None and revoked["token-second"] is None


def test_auth_15_logout_all_revokes_every_token(client, me):
    first, second = _login(me, "install-0001"), _login(me, "install-0002")
    _register(client, first, "token-first")
    _register(client, second, "token-second")
    assert client.post("/api/v1/auth/logout-all", headers=_h(first)).status_code == 204
    assert _targets(me) == []
    with user_context(me.id):
        assert not PushToken.objects.filter(revoked_at=None).exists()


def test_auth_15_deactivated_user_has_no_targets_even_before_the_session_row_is_revoked(client, me):
    _register(client, _login(me, "install-0001"))
    User.objects.filter(id=me.id).update(is_active=False)  # sessions are revoked only on use
    assert _targets(me) == []


@pytest.mark.parametrize(
    "body",
    [
        {"token_type": "apns", "token": EXPO},
        {"token_type": "expo", "token": ""},
        {"token_type": "expo", "token": "has space"},
        {"token_type": "expo", "token": "x" * 256},
        {"token": EXPO},
    ],
)
def test_push_token_body_is_validated(client, me, body):
    pair = _login(me, "install-0001")
    response = client.put(
        "/api/v1/auth/push-token", body, content_type="application/json", headers=_h(pair)
    )
    assert response.status_code == 400


def test_push_token_is_bound_to_the_session_device_not_the_body(client, me):
    pair = _login(me, "install-0001")
    other = _login(me, "install-0002")
    response = client.put(
        "/api/v1/auth/push-token",
        {"token_type": "fcm", "token": "abc", "device_id": str(_device_id(other))},
        content_type="application/json",
        headers=_h(pair),
    )
    assert response.status_code == 204
    assert [t.device_id for t in push_targets_for_user(me.id)] == [_device_id(pair)]


def test_push_token_never_appears_in_any_response(client, me):
    pair = _login(me, "install-0001")
    _register(client, pair, "secret-push-token-value")
    for response in (
        client.get("/api/v1/me", headers=_h(pair)),
        client.get("/api/v1/auth/devices", headers=_h(pair)),
        _register(client, pair, "secret-push-token-value"),
    ):
        assert b"secret-push-token-value" not in response.content


# ---------------------------------------------------------------- RLS and keys


def test_push_tokens_are_isolated_per_user(client, me):
    _register(client, _login(me, "install-0001"), "mine")
    stranger = User.objects.create_user("0712000999")
    _register(client, _login(stranger, "install-0009"), "theirs")
    with user_context(me.id):
        assert list(PushToken.objects.values_list("token", flat=True)) == ["mine"]
    assert list(PushToken.objects.all()) == []  # no user context: nothing


def test_push_token_rows_cannot_be_written_for_another_user(me):
    stranger = User.objects.create_user("0712000999")
    pair = _login(stranger, "install-0009")
    refused = pytest.raises(ProgrammingError, match="row-level security")
    with refused, transaction.atomic(), user_context(me.id):
        PushToken.objects.create(
            user_id=stranger.id,
            device_id=_device_id(pair),
            session_id=pair.session_id,
            token_type="expo",
            token="x",
        )


def test_push_token_device_must_belong_to_its_user(me):
    pair = _login(me, "install-0001")
    stranger = User.objects.create_user("0712000999")
    theirs = _login(stranger, "install-0009")
    with pytest.raises(IntegrityError), transaction.atomic(), user_context(me.id):
        PushToken.objects.create(
            user_id=me.id,
            device_id=_device_id(theirs),
            session_id=pair.session_id,
            token_type="expo",
            token="x",
        )


def test_push_token_foreign_keys():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'accounts_pushtoken'::regclass AND contype = 'f' ORDER BY conname"
        )
        keys = dict(cursor.fetchall())
    assert keys["accounts_pushtoken_device_fk"] == (
        "FOREIGN KEY (device_id, user_id) REFERENCES accounts_device(id, user_id)"
        " ON DELETE RESTRICT"
    )
    assert keys["accounts_pushtoken_session_fk"] == (
        "FOREIGN KEY (session_id) REFERENCES accounts_authsession(id) ON DELETE CASCADE"
    )


def test_one_live_token_per_device_is_enforced_by_the_database(client, me):
    pair = _login(me, "install-0001")
    _register(client, pair, "first")
    with pytest.raises(IntegrityError), transaction.atomic(), user_context(me.id):
        PushToken.objects.create(
            user_id=me.id,
            device_id=_device_id(pair),
            session_id=pair.session_id,
            token_type="expo",
            token="second",
        )


# ---------------------------------------------------------------- push provider interface


def test_push_message_carries_only_a_kind_and_ids():
    from apps.notifications.push import PushMessage

    PushMessage("payment_confirmed", {"payment_id": uuid.uuid4()})
    bad: list[tuple[str, dict[str, Any]]] = [
        ("Payment confirmed!", {}),
        ("payment_confirmed", {"amount": "1500"}),
        ("payment_confirmed", {"name": "Wanjiku"}),
        ("x", {f"k_{c}": uuid.uuid4() for c in "abcde"}),
    ]
    for kind, data in bad:
        with pytest.raises(ValueError):
            PushMessage(kind, data)


def test_fake_push_provider_records_instead_of_sending(settings):
    from apps.notifications import fake
    from apps.notifications.push import PushMessage, PushOutcome, push_provider

    fake.PUSHED.clear()
    provider = push_provider()
    assert provider is not None
    message = PushMessage("payment_confirmed", {"payment_id": uuid.uuid4()})
    assert provider.send("expo", EXPO, message) == PushOutcome.ACCEPTED
    assert [("expo", EXPO, message)] == fake.PUSHED
    fake.PUSHED.clear()
    settings.PUSH_PROVIDER = ""
    assert push_provider() is None


def test_fake_push_provider_is_refused_where_fakes_are_not_allowed(settings):
    from apps.notifications.checks import check_no_fake_senders_outside_dev

    settings.MESSAGING_ALLOW_FAKE = False
    settings.OTP_SENDERS = {}
    settings.FAKE_OTP_OUTBOX_ENABLED = False
    settings.INVITE_SENDER = ""
    settings.MESSAGE_SENDERS = {}
    settings.PUSH_PROVIDER = "apps.notifications.fake.FakePushProvider"
    assert [e.id for e in check_no_fake_senders_outside_dev(None)] == ["notifications.E003"]
