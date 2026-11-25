"""Sessions of an inactive user or a revoked device (AUTH-08), the session tables' foreign keys
and privileges, the per-IP refresh limit, and what may never leave the server: tokens in logs
(SEC-06) and session internals in API responses."""

import io
import json
import logging
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client, RequestFactory
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator

from apps.accounts import sessions
from apps.accounts.authentication import AUTH_ERROR_ATTR
from apps.accounts.middleware import SessionAuthenticationMiddleware
from apps.accounts.models import AuthSession, AuthToken, Device, User
from apps.accounts.sessions import DeviceInfo, TokenPair
from core.api_errors import ApiError
from core.logging import build_logging_config
from core.tenancy import user_context

PHONE = "+254712345678"


def _login(user: User | None = None, installation: str = "install-0001") -> TokenPair:
    user = user or User.objects.create_user(PHONE)
    return sessions.login(user.id, DeviceInfo(installation, "android"), None)


def _refresh_error(token: str) -> str:
    with pytest.raises(ApiError) as info:
        sessions.refresh(token)
    return info.value.error_code


def _middleware_error(access_token: str) -> str | None:
    """The error code the bearer middleware keeps for the request (None if it authenticated)."""
    from django.http import HttpResponse

    request = RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {access_token}")
    SessionAuthenticationMiddleware(lambda _req: HttpResponse())(request)
    error = getattr(request, AUTH_ERROR_ATTR)
    return None if error is None else error.error_code


def _deactivate(pair: TokenPair) -> None:
    User.objects.filter(id=AuthSession.objects.get(id=pair.session_id).user_id).update(
        is_active=False
    )


def _revoke_device(pair: TokenPair) -> None:
    session = AuthSession.objects.get(id=pair.session_id)
    with user_context(session.user_id):
        Device.objects.filter(id=session.device_id).update(revoked_at=timezone.now())


# ---------------------------------------------------------------- item A


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("make_unusable", "reason"),
    [(_deactivate, "user_inactive"), (_revoke_device, "device_revoked")],
    ids=["inactive-user", "revoked-device"],
)
def test_auth_08_refresh_and_middleware_reject_an_inactive_user_or_revoked_device(
    make_unusable, reason
):
    pair = _login()
    assert _middleware_error(pair.access_token) is None  # usable before
    make_unusable(pair)
    assert _middleware_error(pair.access_token) == "session_revoked"
    assert _refresh_error(pair.refresh_token) == "session_revoked"
    session = AuthSession.objects.get(id=pair.session_id)
    assert session.revoked_reason == reason  # the refresh closed the family
    assert session.refresh_generation == 0  # and issued nothing


@pytest.mark.django_db
@pytest.mark.parametrize("make_unusable", [_deactivate, _revoke_device])
def test_grace_replay_after_revocation_of_the_holder_gets_no_pair(make_unusable):
    pair = _login()
    sessions.refresh(pair.refresh_token)
    make_unusable(pair)
    assert _refresh_error(pair.refresh_token) == "session_revoked"  # previous token, in grace


@pytest.mark.django_db
def test_other_devices_of_the_user_keep_working_when_one_is_revoked():
    user = User.objects.create_user(PHONE)
    revoked, kept = _login(user, "install-0001"), _login(user, "install-0002")
    _revoke_device(revoked)
    assert _middleware_error(revoked.access_token) == "session_revoked"
    assert _middleware_error(kept.access_token) is None
    sessions.refresh(kept.refresh_token)


@pytest.mark.django_db
def test_http_answer_for_a_revoked_device_is_401_session_revoked(client: Client):
    pair = _login()
    _revoke_device(pair)
    response = client.post(
        "/api/v1/auth/token/refresh",
        {"refresh_token": pair.refresh_token},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_revoked"


# ---------------------------------------------------------------- item B: keys and grants


def _constraints(table: str) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = %s::regclass AND contype = 'f'",
            [table],
        )
        return dict(cursor.fetchall())


EXPECTED_FOREIGN_KEYS = {
    "accounts_authsession": {
        "accounts_authsession_user_fk": (
            "FOREIGN KEY (user_id) REFERENCES accounts_user(id) ON DELETE RESTRICT"
        ),
        "accounts_authsession_device_fk": (
            "FOREIGN KEY (device_id, user_id) REFERENCES accounts_device(id, user_id)"
            " ON DELETE RESTRICT"
        ),
    },
    "accounts_authtoken": {
        "accounts_authtoken_session_fk": (
            "FOREIGN KEY (session_id) REFERENCES accounts_authsession(id) ON DELETE CASCADE"
        ),
    },
}


@pytest.mark.django_db
@pytest.mark.parametrize("table", sorted(EXPECTED_FOREIGN_KEYS))
def test_session_tables_have_exactly_these_foreign_keys(table):
    assert _constraints(table) == EXPECTED_FOREIGN_KEYS[table]


@pytest.mark.django_db
def test_a_session_cannot_point_at_another_users_device():
    me, other = User.objects.create_user(PHONE), User.objects.create_user("+254712000777")
    pair = _login(me)
    others = _login(other, "install-0002")
    other_device = AuthSession.objects.get(id=others.session_id).device_id
    with pytest.raises(IntegrityError), transaction.atomic():
        AuthSession.objects.filter(id=pair.session_id).update(device_id=other_device)


@pytest.mark.django_db
@pytest.mark.parametrize("model", [AuthSession, AuthToken])
def test_app_rw_cannot_delete_session_rows(model):
    pair = _login()
    sessions.refresh(pair.refresh_token)
    with pytest.raises(Exception, match="permission denied"), transaction.atomic():
        model.objects.all().delete()


# ---------------------------------------------------------------- refresh limit per IP


@pytest.mark.django_db
def test_refresh_is_limited_per_ip(client: Client, settings, redis_db):
    settings.REFRESH_LIMIT_PER_IP = (2, 60)
    statuses = [
        client.post(
            "/api/v1/auth/token/refresh",
            {"refresh_token": "chm_rt_" + "A" * 43},
            content_type="application/json",
        )
        for _ in range(3)
    ]
    assert [r.status_code for r in statuses] == [401, 401, 429]
    body = statuses[2].json()["error"]
    assert body["code"] == "rate_limited" and body["retry_after"] > 0
    other_ip = client.post(
        "/api/v1/auth/token/refresh",
        {"refresh_token": "chm_rt_" + "A" * 43},
        content_type="application/json",
        REMOTE_ADDR="10.9.9.9",
    )
    assert other_ip.status_code == 401


@pytest.mark.django_db
def test_refresh_limit_fails_open_when_redis_is_down(client: Client, monkeypatch):
    from core import ratelimit

    def down(*_args: Any) -> None:
        raise ratelimit.RateLimitUnavailable

    monkeypatch.setattr("apps.accounts.views.hit", down)
    pair = _login()
    response = client.post(
        "/api/v1/auth/token/refresh",
        {"refresh_token": pair.refresh_token},
        content_type="application/json",
    )
    assert response.status_code == 200  # load protection only; safety lives in Postgres


# ---------------------------------------------------------------- never in logs


@pytest.fixture
def rendered_logs():
    """Every log record, rendered by the production formatter (redaction included)."""
    config = build_logging_config(level="DEBUG", json=True)["formatters"]["structured"].copy()
    factory = config.pop("()")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(factory(**config))
    root = logging.getLogger()
    saved = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    quiet = {name: logging.getLogger(name).level for name in ("django.request", "django.server")}
    for name in quiet:
        logging.getLogger(name).setLevel(logging.DEBUG)
    yield stream
    root.removeHandler(handler)
    root.setLevel(saved)
    for name, level in quiet.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.django_db
def test_sec_06_tokens_and_authorization_headers_never_reach_the_logs(
    rendered_logs, monkeypatch, redis_db
):
    from core.logging import get_logger

    pair = _login()
    rotated = sessions.refresh(pair.refresh_token)
    header = f"Bearer {rotated.access_token}"
    secrets_seen = {pair.access_token, pair.refresh_token, rotated.access_token}
    secrets_seen.add(rotated.refresh_token)

    # 1. Ordinary request lines, a 401 and a refresh-token reuse.
    client = Client(raise_request_exception=False)
    client.get("/healthz", HTTP_AUTHORIZATION=header)
    client.post(
        f"/api/v1/auth/token/refresh?token={pair.refresh_token}",
        {"refresh_token": pair.refresh_token},
        content_type="application/json",
        HTTP_AUTHORIZATION=header,
    )

    # 2. An unhandled error whose message and traceback carry the token and the header.
    def boom(_request: Any) -> None:
        raise RuntimeError(f"failed for {header} with {rotated.refresh_token}")

    monkeypatch.setattr("apps.accounts.views._throttle_refresh", boom)
    crashed = client.post(
        "/api/v1/auth/token/refresh",
        {"refresh_token": rotated.refresh_token},
        content_type="application/json",
        HTTP_AUTHORIZATION=header,
    )
    assert crashed.status_code == 500
    # 3. Code that logs them by mistake, as values, extras and an exception.
    log = get_logger("test")
    log.info("oops", authorization=header, value=rotated.access_token)
    logging.getLogger("plain").warning("header was %s", header)
    try:
        raise ValueError(rotated.refresh_token)
    except ValueError:
        log.exception("failed")

    output = rendered_logs.getvalue()
    assert "Internal Server Error" in output and "RuntimeError" in output  # really captured
    assert "Traceback" in output
    for token in secrets_seen:
        assert token not in output
        assert token[len("chm_xx_") :] not in output
    assert "chm_at_" not in output and "chm_rt_" not in output
    for line in output.splitlines():
        record = json.loads(line)
        assert "Bearer rca" not in json.dumps(record)


# ---------------------------------------------------------------- never in responses

INTERNAL_FIELDS = {
    "access_hash",
    "refresh_hash",
    "token_hash",
    "rotation_key_id",
    "refresh_generation",
    "revoked_reason",
    "generation",
    "retired_at",
}


def _property_names(node: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(value)
            names |= _property_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _property_names(item)
    return names


def test_no_documented_response_contains_session_internals():
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    schema = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    names = _property_names(schema)
    assert names, "the schema has properties"  # guards a vacuous pass
    assert names & INTERNAL_FIELDS == set()


@pytest.mark.django_db
def test_no_actual_response_contains_hashes_or_token_rows(client: Client, redis_db):
    pair = _login()
    bodies = []
    for token in (pair.refresh_token, pair.refresh_token, "chm_rt_" + "B" * 43):
        bodies.append(
            client.post(
                "/api/v1/auth/token/refresh",
                {"refresh_token": token},
                content_type="application/json",
            ).content.decode()
        )
    session = AuthSession.objects.get(id=pair.session_id)
    hashes = {session.access_hash, session.refresh_hash}
    hashes |= set(AuthToken.objects.values_list("token_hash", flat=True))
    assert len(hashes) == 3
    for body in bodies:
        assert not any(h in body for h in hashes)
        assert not any(f'"{field}"' in body for field in INTERNAL_FIELDS)
    assert set(json.loads(bodies[0])) == {
        "session_id",
        "access_token",
        "access_expires_in",
        "refresh_token",
        "refresh_expires_in",
    }


def test_internal_fields_list_matches_the_models():
    model_fields = {f.name for f in AuthSession._meta.fields} | {
        f.name for f in AuthToken._meta.fields
    }
    assert model_fields >= INTERNAL_FIELDS


# ---------------------------------------------------------------- compromised token key


@pytest.mark.django_db
def test_compromise_rotation_revokes_only_sessions_derived_with_that_key(settings):
    from io import StringIO

    from django.core.management import CommandError, call_command

    user = User.objects.create_user(PHONE)
    old_key = settings.AUTH_TOKEN_KEYS.split(",")[0].split(":")[0]
    derived_old = _login(user, "install-0001")
    derived_old = sessions.refresh(derived_old.refresh_token)  # derived with the old key
    random_first = _login(user, "install-0002")  # still the random first pair
    settings.AUTH_TOKEN_KEYS = "k9:" + "Z" * 43 + "," + settings.AUTH_TOKEN_KEYS
    derived_new = sessions.refresh(_login(user, "install-0003").refresh_token)

    out = StringIO()
    call_command("revoke_token_key", old_key, "--operator", "ops_oncall", stdout=out)
    assert out.getvalue().startswith("revoked 1 session(s)")
    assert _refresh_error(derived_old.refresh_token) == "session_revoked"
    assert _middleware_error(derived_old.access_token) == "session_revoked"
    assert _middleware_error(random_first.access_token) is None
    assert _middleware_error(derived_new.access_token) is None
    for bad in (["Not A Key!", "--operator", "ops"], [old_key, "--operator", "Ops 1"]):
        with pytest.raises(CommandError):
            call_command("revoke_token_key", *bad)


def _recorded_events(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    from apps.accounts import audit

    recorded: list[tuple[str, dict[str, Any]]] = []

    def spy(kind, **kwargs):
        recorded.append((str(kind), kwargs))
        return audit.record_security_event(kind, **kwargs)

    monkeypatch.setattr("apps.accounts.sessions.record_security_event", spy)
    return recorded


@pytest.mark.django_db
def test_revoke_token_key_writes_a_security_event(settings, monkeypatch):
    from django.core.management import call_command

    key = settings.AUTH_TOKEN_KEYS.split(",")[0].split(":")[0]
    user = User.objects.create_user(PHONE)
    sessions.refresh(_login(user, "install-0001").refresh_token)
    sessions.refresh(_login(user, "install-0002").refresh_token)
    recorded = _recorded_events(monkeypatch)

    call_command("revoke_token_key", key, "--operator", "ops_oncall", stdout=io.StringIO())

    assert recorded == [
        (
            "token_key_revoked",
            {"metadata": {"operator": "ops_oncall", "key_id": key, "count": 2}},
        )
    ]


@pytest.mark.django_db
def test_revoke_token_key_revokes_nothing_if_the_event_cannot_be_written(settings, monkeypatch):
    from django.core.management import call_command

    def broken(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    key = settings.AUTH_TOKEN_KEYS.split(",")[0].split(":")[0]
    pair = sessions.refresh(_login(User.objects.create_user(PHONE)).refresh_token)
    monkeypatch.setattr("apps.accounts.sessions.record_security_event", broken)
    with pytest.raises(RuntimeError):
        call_command("revoke_token_key", key, "--operator", "ops_oncall")
    assert _middleware_error(pair.access_token) is None


@pytest.mark.django_db
def test_retired_token_rows_are_write_once():
    pair = _login()
    sessions.refresh(pair.refresh_token)
    with pytest.raises(Exception, match="permission denied"), transaction.atomic():
        AuthToken.objects.update(generation=5)
