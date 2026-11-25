"""Public interface of the ``lending`` module.

Table-banking loans: eligibility, guarantor pledges, the approval state machine,
amortization schedules and repayments.

Boundary rules:
- Other modules may import ONLY ``apps.lending.services`` and ``apps.lending.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
