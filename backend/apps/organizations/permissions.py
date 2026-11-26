"""Role x action matrix. Internal to organizations; other modules use ``Action`` and
``RequiresAction`` through ``apps.organizations.services``.

Allow/deny only. Step-up (fresh OTP, server PIN, cooling-off) is layered on top by the policy
engine (``apps.organizations.stepup``), which consults this matrix first: a denied action never
reaches step-up.

Every (role, action) pair is written out explicitly. A pair missing from the table, an unknown
role or an unknown action is denied (fail closed); ``tests/test_roles.py`` checks both.
"""

from enum import StrEnum
from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.organizations.roles import Role
from core.tenancy import AUTHENTICATED_CHAMA_ATTR, AUTHENTICATED_USER_ATTR, current_chama_id


class Action(StrEnum):
    # Reads.
    VIEW_MEMBERS = "view_members"  # the roster (every member sees who is in the chama)
    VIEW_CONSTITUTION = "view_constitution"
    VIEW_GROUP_TOTALS = "view_group_totals"  # group liquidity, collection rate, cashbook
    VIEW_SECURITY_EVENTS = "view_security_events"  # the chama's audit trail
    VIEW_INVITATIONS = "view_invitations"  # pending invitations and member phone numbers
    # Writes.
    MANAGE_MEMBERS = "manage_members"  # invite, import a roster, remove
    CHANGE_ROLES = "change_roles"
    EDIT_CONSTITUTION = "edit_constitution"
    RECORD_RECEIPT = "record_receipt"  # manual offline receipt (bank slip)
    WAIVE_FINE = "waive_fine"  # request or approve a fine waiver (two different officials)
    EXPORT_DATA = "export_data"
    PAYMENT_LINKING = "payment_linking"  # the chama's paybill/till and Daraja credentials
    ALLOCATE_PAYMENT = "allocate_payment"  # assign an unallocated M-Pesa payment to a member
    # No step-up: stopping a change must always be easy.
    REVIEW_CHAMA_CHANGES = "review_chama_changes"  # list and cancel pending changes
    FREEZE_CHAMA = "freeze_chama"  # "this wasn't me"


WRITE_ACTIONS = frozenset(
    {
        Action.MANAGE_MEMBERS,
        Action.CHANGE_ROLES,
        Action.EDIT_CONSTITUTION,
        Action.RECORD_RECEIPT,
        Action.WAIVE_FINE,
        Action.EXPORT_DATA,
        Action.PAYMENT_LINKING,
        Action.ALLOCATE_PAYMENT,
        Action.REVIEW_CHAMA_CHANGES,
        Action.FREEZE_CHAMA,
    }
)

ALLOW, DENY = True, False

# fmt: off
ROLE_ACTIONS: dict[Role, dict[Action, bool]] = {
    Role.CHAIR: {
        Action.VIEW_MEMBERS: ALLOW,
        Action.VIEW_CONSTITUTION: ALLOW,
        Action.VIEW_GROUP_TOTALS: ALLOW,
        Action.VIEW_SECURITY_EVENTS: ALLOW,
        Action.VIEW_INVITATIONS: ALLOW,
        Action.MANAGE_MEMBERS: ALLOW,
        Action.CHANGE_ROLES: ALLOW,
        Action.EDIT_CONSTITUTION: ALLOW,
        Action.RECORD_RECEIPT: ALLOW,
        Action.WAIVE_FINE: ALLOW,
        Action.EXPORT_DATA: ALLOW,
        Action.PAYMENT_LINKING: ALLOW,
        Action.ALLOCATE_PAYMENT: ALLOW,
        Action.REVIEW_CHAMA_CHANGES: ALLOW,
        Action.FREEZE_CHAMA: ALLOW,
    },
    Role.TREASURER: {
        Action.VIEW_MEMBERS: ALLOW,
        Action.VIEW_CONSTITUTION: ALLOW,
        Action.VIEW_GROUP_TOTALS: ALLOW,
        Action.VIEW_SECURITY_EVENTS: ALLOW,
        Action.VIEW_INVITATIONS: ALLOW,
        Action.MANAGE_MEMBERS: ALLOW,
        Action.CHANGE_ROLES: DENY,
        Action.EDIT_CONSTITUTION: ALLOW,
        Action.RECORD_RECEIPT: ALLOW,
        Action.WAIVE_FINE: ALLOW,
        Action.EXPORT_DATA: ALLOW,
        Action.PAYMENT_LINKING: ALLOW,
        Action.ALLOCATE_PAYMENT: ALLOW,
        Action.REVIEW_CHAMA_CHANGES: ALLOW,
        Action.FREEZE_CHAMA: ALLOW,
    },
    Role.SECRETARY: {
        Action.VIEW_MEMBERS: ALLOW,
        Action.VIEW_CONSTITUTION: ALLOW,
        Action.VIEW_GROUP_TOTALS: ALLOW,
        Action.VIEW_SECURITY_EVENTS: ALLOW,
        Action.VIEW_INVITATIONS: ALLOW,
        Action.MANAGE_MEMBERS: ALLOW,
        Action.CHANGE_ROLES: DENY,
        Action.EDIT_CONSTITUTION: ALLOW,
        Action.RECORD_RECEIPT: ALLOW,
        Action.WAIVE_FINE: ALLOW,
        Action.EXPORT_DATA: DENY,
        Action.PAYMENT_LINKING: DENY,
        Action.ALLOCATE_PAYMENT: ALLOW,
        Action.REVIEW_CHAMA_CHANGES: ALLOW,
        Action.FREEZE_CHAMA: ALLOW,
    },
    Role.MEMBER: {
        Action.VIEW_MEMBERS: ALLOW,
        Action.VIEW_CONSTITUTION: ALLOW,
        Action.VIEW_GROUP_TOTALS: DENY,
        Action.VIEW_SECURITY_EVENTS: DENY,
        Action.VIEW_INVITATIONS: DENY,
        Action.MANAGE_MEMBERS: DENY,
        Action.CHANGE_ROLES: DENY,
        Action.EDIT_CONSTITUTION: DENY,
        Action.RECORD_RECEIPT: DENY,
        Action.WAIVE_FINE: DENY,
        Action.EXPORT_DATA: DENY,
        Action.PAYMENT_LINKING: DENY,
        Action.ALLOCATE_PAYMENT: DENY,
        Action.REVIEW_CHAMA_CHANGES: DENY,
        Action.FREEZE_CHAMA: DENY,
    },
}
# fmt: on


def is_allowed(role: object, action: object) -> bool:
    """True only for an explicit ALLOW entry. Anything missing or unknown is denied."""
    if not isinstance(role, str) or not isinstance(action, str):
        return False
    try:
        return ROLE_ACTIONS.get(Role(role), {}).get(Action(action), DENY) is ALLOW
    except ValueError:
        return False


class RequiresChama(BasePermission):
    """DRF permission for every endpoint that works on one chama's data.

    An authenticated request without a usable active chama (none chosen, or the membership
    was revoked and the middleware cleared it) answers 404 ``chama_not_available``, the same
    answer switch-chama gives for an unknown chama. No user: denied (401).
    """

    def has_permission(self, request: Request, view: Any) -> bool:
        from apps.organizations.errors import chama_not_available

        if getattr(request, AUTHENTICATED_USER_ATTR, None) is None:
            return False
        chama_id = getattr(request, AUTHENTICATED_CHAMA_ATTR, None)
        if chama_id is None or current_chama_id() != chama_id:
            raise chama_not_available()
        return True


class RequiresAction(RequiresChama):
    """DRF permission: ``permission_classes = [RequiresAction.for_(Action.RECORD_RECEIPT)]``.

    Needs a usable active chama (``RequiresChama``), then reads the role of the
    authenticated user's active membership in the current chama context (both from the
    verified session, never from request input), then applies the step-up policy
    (``apps.organizations.stepup.decide``). No active membership or a role without the action: 403
    ``permission_denied``. A missing or stale factor: 403 ``step_up_required`` (or
    ``pin_not_set``). Freeze and cooling-off are checked by the action inside its transaction.
    """

    action: Action

    @classmethod
    def for_(cls, action: Action) -> type["RequiresAction"]:
        return type(f"Requires_{action.value}", (cls,), {"action": Action(action)})

    def has_permission(self, request: Request, view: Any) -> bool:
        from django.utils import timezone

        from apps.accounts.services import SESSION_ATTR
        from apps.organizations.selectors import current_role
        from apps.organizations.stepup import STEP_UP, chama_state, decide

        if not super().has_permission(request, view):
            return False
        role = current_role(request)
        if role is None:
            return False
        identity = getattr(request._request, SESSION_ATTR, None)
        chama = chama_state(current_chama_id(), identity) if self.action in STEP_UP else None
        decision = decide(role, self.action, identity, timezone.now(), chama)
        if decision.error is not None:
            raise decision.error
        return True
