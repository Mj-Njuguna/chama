"""No OTPs, tokens, full phone numbers or secrets in logs.

Later PRs that log OTP/session flows add end-to-end assertions on top of these.
"""

import io
import json
import logging

import pytest
import structlog

from core.logging import (
    REDACTED,
    build_formatter_processors,
    configure_structlog,
    is_sensitive_key,
    redact_text,
)

PHONE_FORMS = [
    "+254712345678",
    "254712345678",
    "0712345678",
    "0112345678",
    "+254 712 345 678",
    "0712-345-678",
]


@pytest.mark.parametrize("phone", PHONE_FORMS)
def test_phone_numbers_are_masked_to_last_three_digits(phone: str):
    out = redact_text(f"sending otp to {phone} now")
    digits = "".join(c for c in out if c.isdigit())
    assert digits == "678", out


@pytest.mark.parametrize(
    "text",
    [
        "amount_minor=150000",
        "2026-11-19T12:30:45.123456+00:00",
        "2026-11-19 12:30",
        "correlation 550e8400-e29b-41d4-a716-446655440000",
        "invoice 12345678",
        "abc123456789012",
        "v1.123456789.2",
    ],
)
def test_non_phone_numbers_are_left_alone(text: str):
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "key",
    [
        "otp",
        "otp_code",
        "code",
        "pin",
        "new_pin",
        "access_token",
        "refreshToken",
        "token",
        "password",
        "Authorization",
        "HTTP_COOKIE",
        "api_key",
        "apiKey",
        "client_secret",
        "pin_pepper",
        "signature",
        "set_cookie",
    ],
)
def test_sensitive_keys(key: str):
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["status_code", "token_type", "mapping", "shipping", "decode_ms", "path", "event"]
)
def test_non_sensitive_keys(key: str):
    assert not is_sensitive_key(key)


@pytest.fixture
def captured_json_logs():
    """Route the real formatter chain into a buffer and return a reader of JSON lines."""
    configure_structlog()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.stdlib.ExtraAdder(),
            ],
            processors=build_formatter_processors(json=True),
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)

    def read() -> list[dict[str, object]]:
        return [json.loads(line) for line in stream.getvalue().splitlines() if line]

    yield read
    root.removeHandler(handler)
    root.setLevel(old_level)


def test_structlog_event_is_redacted_end_to_end(captured_json_logs):
    structlog.stdlib.get_logger("t").info(
        "otp_sent to +254712345678",
        phone="+254712345678",
        otp="482913",
        nested={"refresh_token": "r" * 43, "note": "call 0712345678"},
        amount_minor=150000,
    )
    [line] = captured_json_logs()
    raw = json.dumps(line)
    for secret in ("+254712345678", "0712345678", "482913", "r" * 43):
        assert secret not in raw
    assert line["otp"] == REDACTED
    assert line["nested"] == {"refresh_token": REDACTED, "note": "call [PHONE…678]"}
    assert line["amount_minor"] == 150000


def test_stdlib_logging_goes_through_the_same_redaction(captured_json_logs):
    logging.getLogger("django.request").warning(
        "bad request from %s", "0712345678", extra={"token": "abc"}
    )
    [line] = captured_json_logs()
    assert "0712345678" not in json.dumps(line)
    assert line["token"] == REDACTED


def test_exception_text_is_redacted(captured_json_logs):
    try:
        raise ValueError("lookup failed for +254712345678")
    except ValueError:
        structlog.stdlib.get_logger("t").exception("boom")
    [line] = captured_json_logs()
    assert "+254712345678" not in json.dumps(line)
    assert "[PHONE…678]" in str(line["exception"])
