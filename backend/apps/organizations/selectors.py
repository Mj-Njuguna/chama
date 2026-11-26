"""Membership reads shared by the organizations module (internal)."""

import uuid
from dataclasses import dataclass

from apps.organizations.models import Membership, MembershipStatus
from apps.organizations.roles import Role
from core.tenancy import (
    AUTHENTICATED_CHAMA_ATTR,
    AUTHENTICATED_USER_ATTR,
    ChamaContextError,
    current_chama_id,
    user_context,
)


@dataclass(frozen=True)
class MembershipSummary:
    """What a user may know about their own memberships: nothing more."""

    chama_id: uuid.UUID
    role: str
    status: str


def memberships_for_user(user_id: uuid.UUID) -> list[MembershipSummary]:
    """The verified user's memberships across all chamas, active, invited and revoked.

    ``user_id`` must come from a verified session or a just-verified OTP, never from request
    input. Runs without a chama context: inside one, RLS would show only that chama's rows, so it
    refuses rather than return a partial list.
    """
    if current_chama_id() is not None:
        raise ChamaContextError("memberships_for_user() must run outside any chama context")
    with user_context(user_id):
        rows = Membership.objects.order_by("created_at", "chama_id").values_list(
            "chama_id", "role", "status"
        )
        return [MembershipSummary(*row) for row in rows]


def active_role(memberships: list[MembershipSummary], chama_id: uuid.UUID) -> str | None:
    for membership in memberships:
        if membership.chama_id == chama_id and membership.status == MembershipStatus.ACTIVE:
            return membership.role
    return None


def current_role(request: object) -> Role | None:
    """Role of the request's authenticated user in the request's active chama, or None.

    Both IDs come from the authentication middleware (the verified session) and the chama
    context it established; nothing is read from headers, body or query string. Fails closed:
    no user, no chama context, a mismatch, or no active membership gives None.
    """
    user_id = getattr(request, AUTHENTICATED_USER_ATTR, None)
    chama_id = getattr(request, AUTHENTICATED_CHAMA_ATTR, None)
    if user_id is None or chama_id is None or current_chama_id() != chama_id:
        return None
    role = (
        Membership.objects.filter(user_id=user_id, status=MembershipStatus.ACTIVE)
        .values_list("role", flat=True)
        .first()
    )
    return Role(role) if role else None


def current_membership_id(request: object) -> uuid.UUID | None:
    """The caller's active membership in the current chama (for "recorded by" fields)."""
    user_id = getattr(request, AUTHENTICATED_USER_ATTR, None)
    chama_id = getattr(request, AUTHENTICATED_CHAMA_ATTR, None)
    if user_id is None or chama_id is None or current_chama_id() != chama_id:
        return None
    return (
        Membership.objects.filter(user_id=user_id, status=MembershipStatus.ACTIVE)
        .values_list("id", flat=True)
        .first()
    )


def is_active_member(membership_id: uuid.UUID) -> bool:
    """Whether ``membership_id`` is an active member of the current chama (RLS-scoped)."""
    return bool(
        Membership.objects.filter(id=membership_id, status=MembershipStatus.ACTIVE).exists()
    )


def active_member_ids() -> list[uuid.UUID]:
    """Every active membership of the current chama (RLS-scoped)."""
    return list(
        Membership.objects.filter(status=MembershipStatus.ACTIVE).values_list("id", flat=True)
    )


@dataclass(frozen=True)
class MemberRef:
    member_id: uuid.UUID
    user_id: uuid.UUID
    member_code: str


def member_directory() -> list[MemberRef]:
    """Every active membership of the current chama with its code (RLS-scoped)."""
    rows = Membership.objects.filter(status=MembershipStatus.ACTIVE).values_list(
        "id", "user_id", "member_code"
    )
    return [MemberRef(*row) for row in rows]


def active_member_by_code(code: str) -> uuid.UUID | None:
    """The active membership whose member code is ``code`` (already normalized), or None."""
    return (
        Membership.objects.filter(member_code=code, status=MembershipStatus.ACTIVE)
        .values_list("id", flat=True)
        .first()
    )


def member_user_id(member_id: uuid.UUID) -> uuid.UUID | None:
    """The user behind a membership of the current chama (any status), or None."""
    return Membership.objects.filter(id=member_id).values_list("user_id", flat=True).first()
