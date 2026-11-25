"""System checks for tenant isolation. Run by `manage.py check`, runserver and
`just deploy-check`; tests/test_tenancy_checks.py runs them against the real settings."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

CHAMA_CONTEXT_MIDDLEWARE = "core.middleware.ChamaContextMiddleware"


@register(Tags.security)
def check_chama_context_middleware(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    middleware = list(settings.MIDDLEWARE)
    auth = getattr(settings, "TENANCY_AUTHENTICATION_MIDDLEWARE", None)
    if CHAMA_CONTEXT_MIDDLEWARE not in middleware:
        return [Error(f"{CHAMA_CONTEXT_MIDDLEWARE} is not in MIDDLEWARE", id="tenancy.E001")]
    if not auth or auth not in middleware:
        return [
            Error(
                "TENANCY_AUTHENTICATION_MIDDLEWARE must name a middleware listed in MIDDLEWARE",
                id="tenancy.E002",
            )
        ]
    if middleware.index(auth) > middleware.index(CHAMA_CONTEXT_MIDDLEWARE):
        return [
            Error(
                f"{auth} must run before {CHAMA_CONTEXT_MIDDLEWARE}",
                hint="Otherwise requests run without the authenticated chama's context.",
                id="tenancy.E003",
            )
        ]
    return []
