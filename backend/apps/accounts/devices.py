"""Devices and push tokens. Internal to identity.

- **Device list and revoke**: a user lists their signed-in devices and revokes
  only their own (``user_isolation`` RLS). Revoking a device ends its sessions
  (``device_revoked``) and revokes its push tokens in the same transaction; its tokens answer
  ``session_revoked`` on the next request or refresh. Another user's device, an unknown ID and
  an already revoked device all answer 404 ``not_found``.
- **Push tokens**: stored per device and session, one live token per device. A token
  is revoked by logout of its session, by logout-all, by revoking its device and by replacing
  it. Whatever the path, :func:`push_targets_for_user` returns a token only while its own row,
  its device and its session are all live and its user is active, so a session ended any other
  way (reuse detection, user deactivation, key compromise, expiry) never yields a target.

Every function here takes the verified ``SessionIdentity`` (or, for push targets, a user ID
from server-side data) and runs in that user's ``user_context`` ( trusted caller).
"""

import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.accounts.audit import MAX_METADATA_INT, record_security_event
from apps.accounts.models import Device, PushToken, SecurityEventKind, User
from apps.accounts.sessions import (
    SessionIdentity,
    devices_with_live_sessions,
    live_session_ids,
    logout,
    logout_all,
    revoke_device_sessions,
)
from core.tenancy import user_context


def _as_user(user_id: uuid.UUID) -> AbstractContextManager[uuid.UUID]:
    """The one ``user_context`` call site of this module (pinned by test_user_context)."""
    return user_context(user_id)  # trusted: a verified session's user, or server-side data


# ---------------------------------------------------------------- devices


@dataclass(frozen=True)
class DeviceSummary:
    id: uuid.UUID
    name: str
    platform: str
    created_at: datetime
    last_seen_at: datetime
    current: bool


def list_devices(identity: SessionIdentity) -> list[DeviceSummary]:
    """The caller's signed-in devices (not revoked, with a live session), most recent first.

    A device whose sessions have all ended (logout, reuse detection, expiry) is not listed:
    there is nothing left to revoke, and a reinstall or terminal wipe shows up as a new device.
    """
    signed_in = devices_with_live_sessions(identity.user_id)
    with _as_user(identity.user_id):
        rows = list(
            Device.objects.filter(revoked_at=None, id__in=signed_in)
            .order_by("-last_seen_at", "id")
            .values_list("id", "name", "platform", "created_at", "last_seen_at")
        )
    return [DeviceSummary(*row, current=row[0] == identity.device_id) for row in rows]


def revoke_device(identity: SessionIdentity, device_id: uuid.UUID) -> None:
    """Revoke one of the caller's devices (possibly the current one), its sessions and tokens."""
    now = timezone.now()
    with transaction.atomic(), _as_user(identity.user_id):
        device = Device.objects.select_for_update().filter(id=device_id, revoked_at=None).first()
        if device is None:
            raise NotFound("No such device.")
        device.revoked_at = now
        device.save(update_fields=["revoked_at"])
        ended = revoke_device_sessions(identity.user_id, device.id, now)
        PushToken.objects.filter(device_id=device.id, revoked_at=None).update(revoked_at=now)
        record_security_event(
            SecurityEventKind.DEVICE_REVOKED,
            user_id=identity.user_id,
            device_id=device.id,
            session_id=identity.session_id,
            metadata={"count": min(ended, MAX_METADATA_INT)},
        )


# ---------------------------------------------------------------- logout with push tokens


def end_session(identity: SessionIdentity) -> None:
    """Logout: the session and the push tokens it registered."""
    with transaction.atomic(), _as_user(identity.user_id):
        logout(identity)
        PushToken.objects.filter(session_id=identity.session_id, revoked_at=None).update(
            revoked_at=timezone.now()
        )


def end_all_sessions(identity: SessionIdentity) -> None:
    """Logout everywhere: every session and every push token of the user."""
    with transaction.atomic(), _as_user(identity.user_id):
        logout_all(identity)
        PushToken.objects.filter(revoked_at=None).update(revoked_at=timezone.now())


# ---------------------------------------------------------------- push tokens


def register_push_token(identity: SessionIdentity, token_type: str, token: str) -> None:
    """Store the current device's push token for this session, replacing any previous one."""
    now = timezone.now()
    with transaction.atomic(), _as_user(identity.user_id):
        live = PushToken.objects.select_for_update().filter(
            device_id=identity.device_id, revoked_at=None
        )
        if live.filter(session_id=identity.session_id, token_type=token_type, token=token).exists():
            return  # the same registration again: nothing to change
        live.update(revoked_at=now)
        PushToken.objects.create(
            user_id=identity.user_id,
            device_id=identity.device_id,
            session_id=identity.session_id,
            token_type=token_type,
            token=token,
        )


def revoke_push_token(identity: SessionIdentity) -> None:
    """Forget the current device's push token (the user turned notifications off)."""
    with _as_user(identity.user_id):
        PushToken.objects.filter(device_id=identity.device_id, revoked_at=None).update(
            revoked_at=timezone.now()
        )


@dataclass(frozen=True)
class PushTarget:
    device_id: uuid.UUID
    token_type: str
    token: str


def push_targets_for_user(user_id: uuid.UUID) -> list[PushTarget]:
    """Where a push for ``user_id`` may go: live tokens on live devices with live sessions.

    ``user_id`` must come from server-side data (for example the owner of the chama a payment
    belongs to), never from request input. Nothing is sent yet. A deactivated user has no
    targets, even before their sessions are revoked on next use.
    """
    if not User.objects.filter(id=user_id, is_active=True).exists():
        return []
    with _as_user(user_id):
        rows = list(
            PushToken.objects.filter(
                revoked_at=None,
                device_id__in=Device.objects.filter(revoked_at=None).values("id"),
            ).values_list("session_id", "device_id", "token_type", "token")
        )
    live = live_session_ids({row[0] for row in rows})
    return [PushTarget(*row[1:]) for row in rows if row[0] in live]


def device_first_seen(user_id: uuid.UUID, device_id: uuid.UUID) -> datetime | None:
    """When this device first signed in. ``user_id`` and ``device_id`` come from the
    verified session."""
    with _as_user(user_id):
        return Device.objects.filter(id=device_id).values_list("created_at", flat=True).first()
