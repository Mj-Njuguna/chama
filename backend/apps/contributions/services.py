"""Public interface of the ``contributions`` module.

Contribution schedules, cycles and obligations, payment allocation and automated late
fines.

Boundary rules:
- Other modules may import ONLY ``apps.contributions.services`` and ``apps.contributions.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
