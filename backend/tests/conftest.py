"""Test database setup.

The test database is created and migrated as app_owner, exactly like production migrations. Every
test then runs as app_rw, the role the app and workers use, so row-level security applies in tests
the same way it does in production. tests/test_db_roles.py asserts the role is really app_rw.
"""

import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager

import pytest
from django.db import connections


@contextmanager
def _connect_as(user: str, password: str) -> Iterator[None]:
    """Reconnect the default alias with other credentials, restoring them afterwards."""
    connection = connections["default"]
    saved = (connection.settings_dict["USER"], connection.settings_dict["PASSWORD"])
    connection.close()
    connection.settings_dict["USER"], connection.settings_dict["PASSWORD"] = user, password
    try:
        yield
    finally:
        connection.close()
        connection.settings_dict["USER"], connection.settings_dict["PASSWORD"] = saved


def _owner_credentials() -> tuple[str, str]:
    try:
        return os.environ["POSTGRES_OWNER_USER"], os.environ["POSTGRES_OWNER_PASSWORD"]
    except KeyError as exc:
        raise RuntimeError(
            "POSTGRES_OWNER_USER/POSTGRES_OWNER_PASSWORD are needed to build the test database; "
            "run tests through `just test`."
        ) from exc


@pytest.fixture(scope="session")
def django_db_setup(
    request: pytest.FixtureRequest,
    django_test_environment: None,
    django_db_blocker,
) -> Generator[None]:
    """Replaces pytest-django's fixture: create/migrate/drop as app_owner, run tests as app_rw."""
    from django.test.utils import setup_databases, teardown_databases

    verbosity = request.config.option.verbose
    with django_db_blocker.unblock(), _connect_as(*_owner_credentials()):
        db_cfg = setup_databases(verbosity=verbosity, interactive=False)

    yield  # tests run here, connected as app_rw (settings default)

    with django_db_blocker.unblock(), _connect_as(*_owner_credentials()):
        teardown_databases(db_cfg, verbosity=verbosity)


@pytest.fixture
def as_owner():
    """Context manager reconnecting as app_owner, for cleaning up committed rows that app_rw is
    not allowed to delete (only in fixtures that commit for real)."""
    return lambda: _connect_as(*_owner_credentials())


# ---------------------------------------------------------------- tenancy fixtures
@pytest.fixture
def two_chamas(db):
    """Chamas A and B, each with probe rows written inside its own chama context."""
    from apps.organizations.models import Chama
    from core.tenancy import chama_context
    from tests.rls_probe.models import ProbeRecord

    a, b = Chama.objects.create(), Chama.objects.create()
    with chama_context(a.id):
        ProbeRecord.objects.create(chama=a, name="a-1")
        ProbeRecord.objects.create(chama=a, name="a-2")
    with chama_context(b.id):
        b_row = ProbeRecord.objects.create(chama=b, name="b-secret")
    return a, b, b_row


@pytest.fixture
def fake_auth(settings):
    """Swap the placeholder authentication middleware for the test session middleware."""
    from tests.rls_probe import auth

    settings.MIDDLEWARE = [
        auth.PATH if m == settings.TENANCY_AUTHENTICATION_MIDDLEWARE else m
        for m in settings.MIDDLEWARE
    ]
    settings.TENANCY_AUTHENTICATION_MIDDLEWARE = auth.PATH
    settings.ROOT_URLCONF = "tests.rls_probe.urls"
    auth.SESSIONS.clear()
    auth.USERS.clear()
    yield auth.SESSIONS
    auth.SESSIONS.clear()
    auth.USERS.clear()


# ---------------------------------------------------------------- redis and messaging fixtures
@pytest.fixture
def redis_db():
    """A clean test Redis database (REDIS_URL ends in /15 under test settings)."""
    from core.redis import get_redis

    client = get_redis()
    assert client.connection_pool.connection_kwargs.get("db") == 15
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture
def fake_senders(redis_db):
    """Fake OTP senders with an empty outbox; BEHAVIOUR reset afterwards."""
    from apps.notifications import fake

    fake.BEHAVIOUR.clear()
    yield fake
    fake.BEHAVIOUR.clear()


@pytest.fixture
def events_spy(monkeypatch):
    """Kinds of security events recorded by apps.accounts.sessions (rows may be invisible)."""
    from apps.accounts import audit

    kinds: list[str] = []
    real = audit.record_security_event

    def spy(kind, **kwargs):
        kinds.append(str(kind))
        return real(kind, **kwargs)

    monkeypatch.setattr("apps.accounts.sessions.record_security_event", spy)
    return kinds
