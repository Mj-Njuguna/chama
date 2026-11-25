"""Public interface of the ``organizations`` module.

Chama workspaces, constitution rules, memberships and roles (chair, treasurer,
secretary, member), member reference codes, roster import and invitations.

Boundary rules:
- Other modules may import ONLY ``apps.organizations.services`` and ``apps.organizations.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
