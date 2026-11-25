"""Chamas and the session's active chama. Internal to organizations.

- **Every request** (the session middleware): a session's active chama is kept only while its
  user holds an ACTIVE membership in it, read through :func:`memberships_for_user` before any
  chama context exists. Otherwise the session's active chama is cleared and the request
  continues without a chama: endpoints that need one answer ``chama_not_available``; ``/me``,
  ``/chamas``, switch-chama, logout and the device endpoints keep working. A removed member
  therefore loses the chama on their next request.
- **Switch-chama**: an unknown chama, a chama without the user's membership, and a revoked
  membership all answer the same 404 ``chama_not_available``, after the same single membership
  query (nothing reveals whether the chama exists).
- **Create chama**: the chama, its constitution (defaults), the creator's chair membership and
  the event in one transaction, at most ``CHAMAS_PER_USER_PER_DAY`` per user per Nairobi day.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.services import (
    SecurityEventKind,
    SessionIdentity,
    clear_active_chama,
    has_pin,
    rate_limited,
    record_security_event,
    set_active_chama,
    set_display_name,
    user_phone,
)
from apps.organizations.codes import new_member_code
from apps.organizations.errors import chama_not_available
from apps.organizations.models import Chama, Constitution, Membership
from apps.organizations.permissions import ROLE_ACTIONS
from apps.organizations.roles import Role
from apps.organizations.selectors import active_role, memberships_for_user
from core.tenancy import chama_context
from core.text import checked_label

CHAMA_NAME_MAX = 120


def checked_identity(identity: SessionIdentity) -> SessionIdentity:
    """``identity`` with its active chama kept only while the user's membership is active.

    Called by the middleware on every authenticated request, before the chama context is set
    (``memberships_for_user`` refuses to run inside one). Clears the session's active chama
    when the membership is gone, so later requests do not repeat the work.
    """
    if identity.active_chama_id is None:
        return identity
    if active_role(memberships_for_user(identity.user_id), identity.active_chama_id):
        return identity
    clear_active_chama(identity)
    return replace(identity, active_chama_id=None)


# ---------------------------------------------------------------- what the user may see


@dataclass(frozen=True)
class MeMembership:
    chama_id: uuid.UUID
    chama_name: str
    role: str
    status: str


@dataclass(frozen=True)
class Me:
    user_id: uuid.UUID
    phone: str
    display_name: str | None
    session_id: uuid.UUID
    session_started_at: datetime | None
    device_id: uuid.UUID
    active_chama_id: uuid.UUID | None
    role: str | None
    member_code: str | None
    memberships: list[MeMembership]
    has_pin: bool
    permissions: list[str]


def permissions_for(role: str | None) -> list[str]:
    """The actions ``role`` allows, in matrix order. A display hint for clients, never
    authorization: the server checks role and step-up on every request."""
    if role is None:
        return []
    return [action.value for action, allowed in ROLE_ACTIONS[Role(role)].items() if allowed]


def _member_code(identity: SessionIdentity, role: str | None) -> str | None:
    if role is None or identity.active_chama_id is None:
        return None
    with chama_context(identity.active_chama_id):
        return (
            Membership.objects.filter(user_id=identity.user_id)
            .values_list("member_code", flat=True)
            .first()
        )


def me(identity: SessionIdentity) -> Me:
    """The caller, their session and their memberships. ``identity`` was checked this request."""
    from apps.accounts.services import display_names

    memberships = memberships_for_user(identity.user_id)
    role = active_role(memberships, identity.active_chama_id) if identity.active_chama_id else None
    names = dict(
        Chama.objects.filter(id__in=[m.chama_id for m in memberships]).values_list("id", "name")
    )
    return Me(
        user_id=identity.user_id,
        phone=user_phone(identity.user_id),
        display_name=display_names([identity.user_id]).get(identity.user_id),
        session_id=identity.session_id,
        session_started_at=identity.started_at,
        device_id=identity.device_id,
        active_chama_id=identity.active_chama_id if role else None,
        role=role,
        member_code=_member_code(identity, role),
        memberships=[
            MeMembership(m.chama_id, names.get(m.chama_id, ""), m.role, m.status)
            for m in memberships
        ],
        has_pin=has_pin(identity.user_id),
        permissions=permissions_for(role),
    )


def update_display_name(identity: SessionIdentity, raw: str) -> None:
    set_display_name(identity.user_id, raw)


# ---------------------------------------------------------------- switch


def switch_chama(identity: SessionIdentity, chama_id: uuid.UUID) -> str:
    """Make ``chama_id`` the session's active chama; return the user's role in it."""
    role = active_role(memberships_for_user(identity.user_id), chama_id)
    if role is None:
        raise chama_not_available()
    with transaction.atomic():
        set_active_chama(identity, chama_id)
        with chama_context(chama_id):
            record_security_event(
                SecurityEventKind.CHAMA_SWITCHED,
                chama_id=chama_id,
                user_id=identity.user_id,
                device_id=identity.device_id,
                session_id=identity.session_id,
            )
    return role


# ---------------------------------------------------------------- create


@dataclass(frozen=True)
class CreatedChama:
    chama_id: uuid.UUID
    name: str
    role: str
    member_code: str


def _nairobi_day(now: datetime) -> tuple[datetime, datetime]:
    local = timezone.localtime(now, ZoneInfo(settings.BUSINESS_TIME_ZONE))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def checked_chama_name(raw: str) -> str:
    return checked_label(raw, "name", CHAMA_NAME_MAX, required=True)


def create_chama(identity: SessionIdentity, name: str) -> CreatedChama:
    """Create a chama chaired by the caller and make it the session's active chama."""
    name = checked_chama_name(name)
    now = timezone.now()
    start, next_start = _nairobi_day(now)
    with transaction.atomic():
        # Serialise one user's concurrent creations so the daily limit cannot be raced.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"chama-create:{identity.user_id}"],
            )
        memberships = memberships_for_user(identity.user_id)
        chaired = [m.chama_id for m in memberships if m.role == Role.CHAIR]
        created_today = Chama.objects.filter(id__in=chaired, created_at__gte=start).count()
        if created_today >= settings.CHAMAS_PER_USER_PER_DAY:
            raise rate_limited(max(1, int((next_start - now).total_seconds())))
        chama = Chama.objects.create(name=name)
        code = new_member_code()
        with chama_context(chama.id):
            Constitution.objects.create(chama=chama, updated_by_user_id=identity.user_id)
            Membership.objects.create(
                chama=chama,
                user_id=identity.user_id,
                role=Role.CHAIR,
                member_code=code,
                joined_at=now,
            )
            record_security_event(
                SecurityEventKind.CHAMA_CREATED,
                chama_id=chama.id,
                user_id=identity.user_id,
                device_id=identity.device_id,
                session_id=identity.session_id,
                metadata={"role": Role.CHAIR.value},
            )
        set_active_chama(identity, chama.id)
    return CreatedChama(chama.id, chama.name, Role.CHAIR.value, code)
