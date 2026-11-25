"""Public interface of the ``ledger`` module.

Double-entry accounting: chart of accounts per chama, member sub-accounts, append-only
journal, manual receipts and two-official expenses.

Boundary rules:
- Other modules may import ONLY ``apps.ledger.services`` and ``apps.ledger.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
