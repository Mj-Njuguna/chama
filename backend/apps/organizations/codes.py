"""Member reference codes: what a member types in the M-Pesa account field.

Six characters from an alphabet without look-alikes (no 0, 1, I or O): five random characters
and one check character, so a mistyped code is refused instead of matching another member. Six
characters fit both the paybill account field and the STK Push account reference. Codes are
unique per chama and never change.
"""

import secrets

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 32 characters
_VALUE = {ch: i for i, ch in enumerate(ALPHABET)}
BODY_LENGTH = 5
CODE_LENGTH = BODY_LENGTH + 1


def _check_char(body: str) -> str:
    # Odd weights are coprime with 32, so any single mistyped character changes the check.
    total = sum((2 * i + 1) * _VALUE[ch] for i, ch in enumerate(body))
    return ALPHABET[total % len(ALPHABET)]


def new_member_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(BODY_LENGTH))
    return body + _check_char(body)


def normalize_member_code(raw: str) -> str:
    """Upper case without spaces, dashes or dots. The result may still be invalid."""
    return "".join(ch for ch in raw.upper() if ch not in " -._")


def is_valid_member_code(code: str) -> bool:
    if len(code) != CODE_LENGTH or any(ch not in _VALUE for ch in code):
        return False
    return _check_char(code[:BODY_LENGTH]) == code[BODY_LENGTH]
