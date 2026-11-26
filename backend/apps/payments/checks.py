"""System checks for payments: the fake gateway never runs where fakes are not allowed."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register


@register(Tags.security)
def check_no_fake_gateway_outside_dev(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    if settings.MESSAGING_ALLOW_FAKE:  # dev and tests
        return []
    errors: list[CheckMessage] = []
    if settings.MPESA_GATEWAY.startswith("apps.payments.fake."):
        errors.append(
            Error(
                "the fake M-Pesa gateway is configured where fakes are not allowed",
                id="payments.E001",
            )
        )
    if settings.FAKE_MPESA_AUTOCOMPLETE_SECONDS:
        errors.append(
            Error(
                "fake STK auto-completion is enabled where fakes are not allowed",
                id="payments.E002",
            )
        )
    return errors
