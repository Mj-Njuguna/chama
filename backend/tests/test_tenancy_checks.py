""": the app refuses to start unless authentication runs before the chama context."""

import importlib
import sys

import pytest
from django.core.management import call_command

from core.checks import CHAMA_CONTEXT_MIDDLEWARE, check_chama_context_middleware

AUTH = "apps.accounts.middleware.SessionAuthenticationMiddleware"
OTHER = "django.middleware.common.CommonMiddleware"


def _ids(settings, middleware, auth=AUTH) -> list[str]:
    settings.MIDDLEWARE = middleware
    settings.TENANCY_AUTHENTICATION_MIDDLEWARE = auth
    return [str(message.id) for message in check_chama_context_middleware(None)]


def test_real_settings_pass():
    assert check_chama_context_middleware(None) == []
    call_command("check", fail_level="ERROR")  # raises SystemCheckError on any error


def test_correct_order_passes(settings):
    assert _ids(settings, [OTHER, AUTH, CHAMA_CONTEXT_MIDDLEWARE]) == []


def test_auth_after_chama_context_fails(settings):
    assert _ids(settings, [CHAMA_CONTEXT_MIDDLEWARE, AUTH]) == ["tenancy.E003"]


def test_missing_auth_middleware_fails(settings):
    assert _ids(settings, [OTHER, CHAMA_CONTEXT_MIDDLEWARE]) == ["tenancy.E002"]
    assert _ids(settings, [AUTH, CHAMA_CONTEXT_MIDDLEWARE], auth=None) == ["tenancy.E002"]


def test_missing_chama_context_middleware_fails(settings):
    assert _ids(settings, [AUTH, OTHER]) == ["tenancy.E001"]


@pytest.mark.parametrize("module_name", ["config.settings.dev", "config.settings.prod"])
def test_every_environment_lists_both_middlewares_in_order(module_name, monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "k" * 64)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("AUDIT_HASH_KEYS", "t1:" + "A" * 43)
    monkeypatch.setenv("OTP_PEPPER_KEYS", "t1:" + "B" * 43)
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    monkeypatch.setenv("AUTH_TOKEN_KEYS", "t1:" + "C" * 43)
    monkeypatch.setenv("PIN_PEPPER_KEYS", "t1:" + "D" * 43)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEYS", "t1:" + "E" * 43)
    names = (module_name, "config.settings.base")
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    try:
        module = importlib.import_module(module_name)
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    auth = module.TENANCY_AUTHENTICATION_MIDDLEWARE
    assert module.MIDDLEWARE.index(auth) < module.MIDDLEWARE.index(CHAMA_CONTEXT_MIDDLEWARE)
