"""Public interface of the ``notifications`` module.

WhatsApp, SMS and push delivery, reminders, the in-app group feed and group webhooks.

Boundary rules:
- Other modules may import ONLY ``apps.notifications.services`` and ``apps.notifications.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
