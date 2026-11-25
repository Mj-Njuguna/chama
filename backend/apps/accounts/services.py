"""Public interface of the ``accounts`` module.

Phone + OTP authentication, devices, sessions, step-up PIN, user profiles and KYC fields.

Boundary rules:
- Other modules may import ONLY ``apps.accounts.services`` and ``apps.accounts.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
