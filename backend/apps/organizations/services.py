"""Public interface of the ``organizations`` module.

Chama workspaces, constitution rules, memberships and roles (chair, treasurer, secretary,
member), member reference codes, roster import and invitations, the role x action matrix and
the step-up policy.

Boundary rules:
- Other modules may import ONLY ``apps.organizations.services`` and
  ``apps.organizations.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""

from apps.organizations.chamas import checked_identity
from apps.organizations.codes import is_valid_member_code, normalize_member_code
from apps.organizations.constitution import rules_for_chama
from apps.organizations.errors import chama_not_available
from apps.organizations.pending import official_of_frozen_chama
from apps.organizations.permissions import Action, RequiresAction, RequiresChama
from apps.organizations.roles import OFFICIALS, Role
from apps.organizations.selectors import (
    MembershipSummary,
    active_member_ids,
    current_membership_id,
    current_role,
    is_active_member,
    memberships_for_user,
)
from apps.organizations.stepup import role_allows as is_allowed  # refuses step-up actions

__all__ = [
    "OFFICIALS",
    "Action",
    "MembershipSummary",
    "RequiresAction",
    "RequiresChama",
    "Role",
    "active_member_ids",
    "chama_not_available",
    "checked_identity",
    "current_membership_id",
    "current_role",
    "is_active_member",
    "is_allowed",
    "is_valid_member_code",
    "memberships_for_user",
    "normalize_member_code",
    "official_of_frozen_chama",
    "rules_for_chama",
]
