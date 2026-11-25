"""Public interface of the ``reports`` module.

Member statements (PDF), audit reports and the year-end shareout calculator.

Boundary rules:
- Other modules may import ONLY ``apps.reports.services`` and ``apps.reports.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
