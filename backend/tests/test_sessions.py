"""Sessions and tokens: rotation, the grace window (AUTH-16), reuse detection
(AUTH-07), concurrency, absolute age, the middleware, and the access rules. Login and logout
over HTTP are tested in test_login.py."""

import re
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.db import connection
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts import sessions
from apps.accounts.models import AuthSession, AuthToken, Device, User
from apps.accounts.sessions import DeviceInfo, TokenPair
from apps.organizations.models import Chama, Membership
from apps.organizations.roles import Role
from core.api_errors import ApiError
from core.tenancy import chama_context, current_chama_id, current_user_id, user_context

BACKEND = Path(__file__).resolve().parent.parent
DEVICE = {"installation_id": "install-0001", "platform": "android", "name": "Pixel"}
PHONE = "+254712345678"


def _error(call: Any) -> ApiError:
    with pytest.raises(ApiError) as info:
        call()
    return info.value


def _login(user_id: uuid.UUID | None = None, chama: uuid.UUID | None = None) -> TokenPair:
    user_id = user_id or User.objects.create_user(PHONE).id
    return sessions.login(user_id, DeviceInfo("install-0001", "android"), chama)


def _session(pair: TokenPair) -> AuthSession:
    return AuthSession.objects.get(id=pair.session_id)


def _age_rotation(monkeypatch: pytest.MonkeyPatch, seconds: int) -> None:
    """Move the clock forward (retired token rows are write-once, so they cannot be back-dated)."""
    later = timezone.now() + timedelta(seconds=seconds)
    monkeypatch.setattr(timezone, "now", lambda: later)


# ---------------------------------------------------------------- tokens are opaque and hashed


@pytest.mark.django_db
def test_only_token_hashes_are_stored():
    pair = _login()
    rotated = sessions.refresh(pair.refresh_token)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT string_agg(row_to_json(s)::text, '') FROM accounts_authsession s"
            " UNION ALL SELECT string_agg(row_to_json(t)::text, '') FROM accounts_authtoken t"
        )
        stored = "".join(row[0] or "" for row in cursor.fetchall())
    for token in (
        pair.access_token,
        pair.refresh_token,
        rotated.access_token,
        rotated.refresh_token,
    ):
        assert token not in stored and token[7:] not in stored


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["", "chm_rt_short", "chm_at_" + "A" * 43, "x" * 50, None, 5])
def test_malformed_or_unknown_refresh_tokens_are_invalid(token: object):
    assert _error(lambda: sessions.refresh(token)).error_code == "invalid_token"
    assert _error(lambda: sessions.refresh("chm_rt_" + "A" * 43)).error_code == "invalid_token"


# ---------------------------------------------------------------- rotation


@pytest.mark.django_db
def test_refresh_rotates_and_the_old_access_token_stops_working():
    pair = _login()
    rotated = sessions.refresh(pair.refresh_token)
    assert rotated.refresh_token != pair.refresh_token
    assert rotated.access_token != pair.access_token
    assert _session(pair).refresh_generation == 1
    assert sessions.authenticate_access(rotated.access_token).session_id == pair.session_id
    assert _error(lambda: sessions.authenticate_access(pair.access_token)).error_code == (
        "invalid_token"
    )


@pytest.mark.django_db
def test_auth_16_previous_token_within_grace_returns_the_same_pair():
    pair = _login()
    first = sessions.refresh(pair.refresh_token)
    again = sessions.refresh(pair.refresh_token)  # e.g. the response was lost
    assert (again.access_token, again.refresh_token) == (first.access_token, first.refresh_token)
    assert _session(pair).refresh_generation == 1  # no second rotation, no second pair
    assert AuthToken.objects.count() == 1
    assert _session(pair).revoked_at is None


@pytest.mark.django_db
def test_auth_16_previous_token_after_grace_revokes_the_family(events_spy, monkeypatch):
    pair = _login()
    first = sessions.refresh(pair.refresh_token)
    _age_rotation(monkeypatch, seconds=11)
    assert _error(lambda: sessions.refresh(pair.refresh_token)).error_code == "refresh_reused"
    session = _session(pair)
    assert session.revoked_at is not None and session.revoked_reason == "refresh_reused"
    assert _error(lambda: sessions.refresh(first.refresh_token)).error_code == "session_revoked"
    assert _error(lambda: sessions.authenticate_access(first.access_token)).error_code == (
        "session_revoked"
    )
    assert "refresh_reused" in events_spy


@pytest.mark.django_db
def test_auth_07_reuse_after_the_successor_was_used_revokes_the_family():
    pair = _login()
    first = sessions.refresh(pair.refresh_token)
    second = sessions.refresh(first.refresh_token)  # the successor has itself been rotated
    # Even inside the time window, the previous-but-one token is reuse.
    assert _error(lambda: sessions.refresh(pair.refresh_token)).error_code == "refresh_reused"
    assert _error(lambda: sessions.refresh(second.refresh_token)).error_code == "session_revoked"


@pytest.mark.django_db
def test_auth_07_attacker_reuse_kills_the_victims_session_too(monkeypatch):
    pair = _login()
    victim = sessions.refresh(pair.refresh_token)  # the victim rotates
    _age_rotation(monkeypatch, seconds=60)
    assert _error(lambda: sessions.refresh(pair.refresh_token)).error_code == "refresh_reused"
    assert _error(lambda: sessions.refresh(victim.refresh_token)).error_code == "session_revoked"


@pytest.mark.django_db
def test_grace_window_length_is_a_setting(settings, monkeypatch):
    settings.REFRESH_GRACE_SECONDS = 30
    pair = _login()
    first = sessions.refresh(pair.refresh_token)
    _age_rotation(monkeypatch, seconds=25)
    assert sessions.refresh(pair.refresh_token).refresh_token == first.refresh_token
    settings.REFRESH_GRACE_SECONDS = 0
    assert _error(lambda: sessions.refresh(pair.refresh_token)).error_code == "refresh_reused"


@pytest.mark.django_db
def test_token_key_rotation_mid_grace_still_returns_the_same_pair(settings):
    pair = _login()
    first = sessions.refresh(pair.refresh_token)
    settings.AUTH_TOKEN_KEYS = "k9:" + "Z" * 43 + "," + settings.AUTH_TOKEN_KEYS  # new key first
    again = sessions.refresh(pair.refresh_token)
    assert again.refresh_token == first.refresh_token
    newer = sessions.refresh(first.refresh_token)  # the next rotation uses the new key
    assert _session(pair).rotation_key_id == "k9" and newer.refresh_token != first.refresh_token


@pytest.mark.django_db
def test_token_key_removed_mid_grace_fails_safe(settings):
    pair = _login()
    sessions.refresh(pair.refresh_token)
    settings.AUTH_TOKEN_KEYS = "k9:" + "Z" * 43  # the deriving key is gone
    assert _error(lambda: sessions.refresh(pair.refresh_token)).error_code == "session_revoked"
    session = _session(pair)
    assert session.revoked_reason == "key_unavailable"  # no pair issued; the family is closed


# ---------------------------------------------------------------- concurrency


@pytest.fixture
def committed_session(django_db_setup, django_db_blocker, as_owner):
    """A session committed for real, so two threads (two connections) can race on it."""
    with django_db_blocker.unblock():
        user = User.objects.create_user("+254712999001")
        pair = sessions.login(user.id, DeviceInfo("install-race", "android"), None)
        yield pair
        with as_owner():  # app_rw may not delete session rows
            AuthSession.objects.filter(id=pair.session_id).delete()  # tokens cascade
        with user_context(user.id):  # the device is under user RLS, even for app_owner
            Device.objects.filter(user_id=user.id).delete()
        User.objects.filter(id=user.id).delete()


def test_concurrent_refreshes_rotate_once(committed_session, django_db_blocker, monkeypatch):
    pair = committed_session
    real = sessions._derived_pair
    inside = threading.Event()

    def slow_derive(key: bytes, previous: str) -> tuple[str, str]:
        if not inside.is_set():  # the first rotation holds the row lock for a while
            inside.set()
            threading.Event().wait(0.4)
        return real(key, previous)

    monkeypatch.setattr(sessions, "_derived_pair", slow_derive)
    results: list[Any] = [None, None]

    def worker(index: int) -> None:
        try:
            if index == 1:
                inside.wait(5)  # start while the first holds the lock
            results[index] = sessions.refresh(pair.refresh_token)
        except Exception as exc:  # reported by the assertions below
            results[index] = exc
        finally:
            connection.close()

    with django_db_blocker.unblock():
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        first, second = results
        assert isinstance(first, TokenPair) and isinstance(second, TokenPair), results
        assert (first.access_token, first.refresh_token) == (
            second.access_token,
            second.refresh_token,
        )
        session = AuthSession.objects.get(id=pair.session_id)
        assert session.refresh_generation == 1 and session.revoked_at is None
        assert AuthToken.objects.filter(session_id=pair.session_id).count() == 1


# ---------------------------------------------------------------- lifetimes


@pytest.mark.django_db
def test_access_tokens_expire():
    pair = _login()
    AuthSession.objects.update(access_expires_at=timezone.now() - timedelta(seconds=1))
    assert _error(lambda: sessions.authenticate_access(pair.access_token)).error_code == (
        "token_expired"
    )


@pytest.mark.django_db
def test_sessions_have_an_absolute_maximum_age(settings):
    pair = _login()
    session = _session(pair)
    age = session.absolute_expires_at - session.created_at
    assert abs(age - timedelta(days=90)) < timedelta(seconds=2)
    AuthSession.objects.update(absolute_expires_at=timezone.now() - timedelta(seconds=1))
    assert _error(lambda: sessions.refresh(pair.refresh_token)).error_code == "session_expired"
    assert _error(lambda: sessions.authenticate_access(pair.access_token)).error_code == (
        "session_expired"
    )


@pytest.mark.django_db
def test_token_lifetimes_never_pass_the_absolute_limit(settings):
    settings.SESSION_MAX_AGE_SECONDS = 600
    pair = _login()
    assert pair.access_expires_in <= 600 and pair.refresh_expires_in <= 600


# ---------------------------------------------------------------- the middleware


def _through_middleware(request: Any) -> dict[str, Any]:
    from django.http import HttpResponse

    from apps.accounts.middleware import SessionAuthenticationMiddleware

    seen: dict[str, Any] = {}

    def view(req: Any) -> HttpResponse:
        seen.update(
            user=req.authenticated_user_id,
            chama=req.authenticated_chama_id,
            context_user=current_user_id(),
            context_chama=current_chama_id(),
        )
        return HttpResponse()

    SessionAuthenticationMiddleware(view)(request)
    return seen


@pytest.mark.django_db
def test_middleware_sets_identity_and_user_context_from_the_token_only():
    user = User.objects.create_user(PHONE)
    chama = Chama.objects.create().id
    with chama_context(chama):
        Membership.objects.create(chama_id=chama, user=user, role=Role.CHAIR)
    pair = _login(user.id, chama)
    other = uuid.uuid4()
    request = RequestFactory().post(
        f"/x?user_id={other}",
        {"user_id": str(other), "chama_id": str(other)},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {pair.access_token}",
        HTTP_X_USER_ID=str(other),
        HTTP_X_CHAMA_ID=str(other),
    )
    seen = _through_middleware(request)
    assert seen["user"] == user.id and seen["chama"] == chama
    assert seen["context_user"] == user.id  # user_context is set for the request
    assert seen["context_chama"] is None  # chama context is ChamaContextMiddleware's job


@pytest.mark.django_db
def test_middleware_without_a_valid_token_sets_no_identity():
    for headers in ({}, {"Authorization": "Bearer chm_at_" + "A" * 43}):
        seen = _through_middleware(RequestFactory().get("/x", headers=headers))
        assert seen == {
            "user": None,
            "chama": None,
            "context_user": None,
            "context_chama": None,
        }


# ---------------------------------------------------------------- access rules


@pytest.mark.django_db
def test_devices_are_visible_only_to_their_own_user():
    me, other = User.objects.create_user(PHONE), User.objects.create_user("+254712000777")
    _login(me.id)
    _login(other.id)
    assert Device.objects.count() == 0  # no user context: nothing
    with user_context(me.id):
        assert [d.user_id for d in Device.objects.all()] == [me.id]


# Item 9: accounts_authsession and accounts_authtoken are reached only through one module.
SESSION_TABLE_MODULES = {"apps/accounts/models.py", "apps/accounts/sessions.py"}


def test_session_tables_are_used_only_by_the_sessions_module():
    pattern = re.compile(r"\b(AuthSession|AuthToken|accounts_authsession|accounts_authtoken)\b")
    found = set()
    for top in ("apps", "core", "config"):
        for path in (BACKEND / top).rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/migrations/" in rel or rel == "core/tenancy.py":  # RLS allowlist names them
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                found.add(rel)
    assert found == SESSION_TABLE_MODULES
