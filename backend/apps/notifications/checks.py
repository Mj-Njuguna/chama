"""System checks for notifications: fakes never run where they are not allowed."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

FAKE_MODULE = "apps.notifications.fake."


@register(Tags.security)
def check_no_fake_senders_outside_dev(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    if settings.MESSAGING_ALLOW_FAKE:
        return []
    errors: list[CheckMessage] = []
    fakes = [ch for ch, path in settings.OTP_SENDERS.items() if path.startswith(FAKE_MODULE)]
    if fakes:
        errors.append(
            Error(
                f"fake OTP senders configured for {sorted(fakes)} where fakes are not allowed",
                id="notifications.E001",
            )
        )
    if settings.PUSH_PROVIDER.startswith(FAKE_MODULE):
        errors.append(
            Error(
                "the fake push provider is configured where fakes are not allowed",
                id="notifications.E003",
            )
        )
    if settings.INVITE_SENDER.endswith(".FakeInviteSender"):
        errors.append(
            Error(
                "the fake invite sender is configured where fakes are not allowed",
                id="notifications.E004",
            )
        )
    if settings.FAKE_OTP_OUTBOX_ENABLED:
        errors.append(
            Error(
                "the fake OTP outbox is enabled where fakes are not allowed",
                id="notifications.E002",
            )
        )
    return errors
