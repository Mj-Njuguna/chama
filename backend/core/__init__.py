"""Shared kernel.

Rules:
- ``core`` has no models and imports no module under ``apps``.
- It holds cross-cutting plumbing only: env parsing, logging, correlation IDs, health checks.
"""
