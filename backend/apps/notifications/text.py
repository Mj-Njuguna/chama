"""Plain text helpers for member messages (internal; no imports from other modules)."""


def format_kes(amount_minor: int) -> str:
    """``KES 5,000.00`` for message text (members read it in WhatsApp or SMS)."""
    shillings, cents = divmod(amount_minor, 100)
    return f"KES {shillings:,}.{cents:02d}"
