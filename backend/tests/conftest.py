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


# ---------------------------------------------------------------- redis
@pytest.fixture
def redis_db():
    """A clean test Redis database (REDIS_URL ends in /15 under test settings)."""
    from core.redis import get_redis

    client = get_redis()
    assert client.connection_pool.connection_kwargs.get("db") == 15
    client.flushdb()
    yield client
    client.flushdb()
