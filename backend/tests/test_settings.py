import importlib
import sys

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_MODULES = ("config.settings.prod", "config.settings.base")
# A syntactically valid ring for tests that are about something else.
_RINGS = {
    "AUDIT_HASH_KEYS": "t1:" + "A" * 43,
    "OTP_PEPPER_KEYS": "t1:" + "B" * 43,
    "AUTH_TOKEN_KEYS": "t1:" + "C" * 43,
    "PIN_PEPPER_KEYS": "t1:" + "D" * 43,
    "FIELD_ENCRYPTION_KEYS": "t1:" + "E" * 43,
}
_REQUIRED = {**_RINGS, "TRUSTED_PROXY_COUNT": "1"}


def _import_prod(monkeypatch, **env: str):
    for key in ("DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", *_REQUIRED):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_REQUIRED, **env}.items():
        if value:
            monkeypatch.setenv(key, value)
    saved = {name: sys.modules.pop(name) for name in _MODULES if name in sys.modules}
    try:
        return importlib.import_module("config.settings.prod")
    finally:
        for name in _MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_prod_refuses_to_start_without_secret_key(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        _import_prod(monkeypatch, DJANGO_ALLOWED_HOSTS="api.example.com")


def test_prod_refuses_to_start_without_allowed_hosts(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="ALLOWED_HOSTS"):
        _import_prod(monkeypatch, DJANGO_SECRET_KEY="k" * 64)


def test_prod_is_hardened(monkeypatch):
    prod = _import_prod(
        monkeypatch, DJANGO_SECRET_KEY="k" * 64, DJANGO_ALLOWED_HOSTS="api.example.com"
    )
    assert prod.DEBUG is False
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SECURE_HSTS_SECONDS >= 31536000
    # Django admin is never installed in prod.
    assert "django.contrib.admin" not in prod.INSTALLED_APPS


def test_db_connect_as_rejects_unknown_values(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DB_CONNECT_AS"):
        _import_prod(
            monkeypatch,
            DJANGO_SECRET_KEY="k" * 64,
            DJANGO_ALLOWED_HOSTS="api.example.com",
            DB_CONNECT_AS="superuser",
        )


def test_app_connects_as_app_rw_by_default():
    assert settings.DB_CONNECT_AS == "app"
    assert settings.DATABASES["default"]["USER"] == "app_rw"


def test_time_defaults():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"
    assert settings.BUSINESS_TIME_ZONE == "Africa/Nairobi"


_GOOD = {"DJANGO_SECRET_KEY": "k" * 64, "DJANGO_ALLOWED_HOSTS": "api.example.com"}


@pytest.mark.parametrize("ring", sorted(_RINGS))
def test_prod_refuses_to_start_without_each_key_ring(monkeypatch, ring: str):
    with pytest.raises(ImproperlyConfigured, match=ring):
        _import_prod(monkeypatch, **_GOOD, **{ring: ""})


@pytest.mark.parametrize("value", ["no-key-id", "k1:short", "K1:" + "A" * 43, "k1:!!!!"])
def test_prod_refuses_a_malformed_key_ring(monkeypatch, value: str):
    with pytest.raises(ImproperlyConfigured, match="AUDIT_HASH_KEYS"):
        _import_prod(monkeypatch, **_GOOD, AUDIT_HASH_KEYS=value)


def test_prod_refuses_to_start_without_an_explicit_proxy_count(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="TRUSTED_PROXY_COUNT"):
        _import_prod(monkeypatch, **_GOOD, TRUSTED_PROXY_COUNT="")
    assert _import_prod(monkeypatch, **_GOOD, TRUSTED_PROXY_COUNT="0").TRUSTED_PROXY_COUNT == 0
