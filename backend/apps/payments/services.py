"""Public interface of the ``payments`` module.

M-Pesa integration on the chama's own paybill or till: C2B confirmations, STK Push,
idempotent ingestion, matching, the unallocated payment resolver, and provider adapters
(bank adapters plug in behind the same interface).

Boundary rules:
- Other modules may import ONLY ``apps.payments.services`` and ``apps.payments.events``.
- Everything else in this package (models, selectors, providers, views) is internal.
- Functions here take and return plain values or small dataclasses, never querysets or model
  instances of this module, so callers can't couple to its tables.
"""
