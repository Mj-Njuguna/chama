"""Public interface of the ``accounts`` module.

Users, phone + OTP login, devices, auth sessions, the server step-up PIN, push tokens and the
append-only security event log.

Boundary rules:
- Other modules may import ONLY ``apps.accounts.services`` and ``apps.accounts.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""

import uuid

from apps.accounts.audit import (
    MAX_METADATA_INT,
    InvalidAuditMetadata,
    SecurityEventSummary,
    chama_security_events,
    record_security_event,
    subject_hashes,
)
from apps.accounts.authentication import SESSION_ATTR
from apps.accounts.devices import PushTarget, device_first_seen, push_targets_for_user
from apps.accounts.models import SecurityEventKind
from apps.accounts.otp import rate_limited
from apps.accounts.pin import has_pin, pin_not_set, reset_effective_at, step_up_required
from apps.accounts.sessions import SessionIdentity, clear_active_chama, set_active_chama
from core.text import checked_label

__all__ = [
    "MAX_METADATA_INT",
    "SESSION_ATTR",
    "InvalidAuditMetadata",
    "PushTarget",
    "SecurityEventKind",
    "SecurityEventSummary",
    "SessionIdentity",
    "chama_security_events",
    "checked_display_name",
    "clear_active_chama",
    "device_first_seen",
    "display_names",
    "has_pin",
    "phones_for",
    "pin_not_set",
    "push_targets_for_user",
    "rate_limited",
    "record_security_event",
    "reset_effective_at",
    "set_active_chama",
    "set_display_name",
    "step_up_required",
    "subject_hashes",
    "user_for_phone",
    "user_phone",
]


def user_for_phone(phone_e164: str) -> uuid.UUID:
    """The user ID for a normalized number, creating the user if needed (an invitation creates
    the user before they first sign in: users exist before memberships)."""
    from apps.accounts.models import User

    user = User.objects.filter(phone=phone_e164).first() or User.objects.create_user(phone_e164)
    return user.id


def user_phone(user_id: uuid.UUID) -> str:
    """A user's own number (E.164). Callers must never log or display it in full."""
    from apps.accounts.models import User

    return User.objects.values_list("phone", flat=True).get(id=user_id)


def phones_for(user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    from apps.accounts.models import User

    return dict(User.objects.filter(id__in=set(user_ids)).values_list("id", "phone"))


def display_names(user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    """Display names of users, for "changed by" labels (``None`` when not set). Never phones."""
    from apps.accounts.models import User

    rows = User.objects.filter(id__in=set(user_ids)).values_list("id", "display_name")
    return {user_id: (name or None) for user_id, name in rows}


DISPLAY_NAME_MAX = 50


def checked_display_name(raw: str) -> str:
    """1 to 50 characters after trimming, no invisible or control characters. Never logged."""
    return checked_label(raw, "display_name", DISPLAY_NAME_MAX, required=True)


def set_display_name(user_id: uuid.UUID, raw: str) -> None:
    """Set a user's own display name. ``user_id`` comes only from the verified session."""
    from apps.accounts.models import User

    User.objects.filter(id=user_id).update(display_name=checked_display_name(raw))
