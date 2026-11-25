"""Structured JSON logging (structlog) with correlation IDs and redaction.

Every log line, whether it comes from structlog or the stdlib (Django, Celery, gunicorn), goes
through the same formatter chain, so redaction cannot be bypassed by using ``logging`` directly.

Redaction rules:
- Values under sensitive keys (OTP, PIN, code, tokens, secrets, passwords, auth headers, cookies)
  are replaced with ``[REDACTED]``. Keys are split into words, so ``otp_code`` and ``refreshToken``
  match but ``mapping`` does not.
- Phone numbers inside any string value (including the message) are masked to their last three
  digits: ``+254712345678`` -> ``[PHONE…678]``. Enough to tell numbers apart in support.
- Session tokens (``chm_at_…``, ``chm_rt_…``) and ``Bearer <value>`` inside any string value,
  including formatted exception tracebacks, become ``chm_[REDACTED]`` / ``Bearer [REDACTED]``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED = "[REDACTED]"

_SENSITIVE_WORDS = frozenset(
    {
        "otp",
        "pin",
        "code",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "authorization",
        "auth",
        "cookie",
        "apikey",
        "signature",
        "pepper",
        "credential",
        "credentials",
        "csrf",
        "sessionid",
        "bearer",
    }
)
_SENSITIVE_PHRASES = ("api_key", "private_key", "session_key", "set_cookie")
# Keys that contain a sensitive word but are safe and useful for debugging.
_SAFE_KEYS = frozenset({"status_code", "token_type", "error_code", "country_code"})

# Phone numbers: contiguous 9-15 digits (optionally + prefixed) not embedded in a longer
# word/number/ID, plus the common grouped Kenyan forms ("+254 712 345 678", "0712-345-678").
_PHONE = re.compile(
    r"(?<![\w.+-])(?:"
    r"\+?\d{9,15}"
    r"|\+?254[ -]\d{3}[ -]?\d{3}[ -]?\d{3}"
    r"|0[17]\d{2}[ -]\d{3}[ -]?\d{3}"
    r")(?![\w.-])"
)
_SESSION_TOKEN = re.compile(r"chm_[a-z]{2}_[A-Za-z0-9_-]*")
_BEARER_VALUE = re.compile(r"(?i)\b(bearer)\s+[^\s'\",;]+")
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"[PHONE…{digits[-3:]}]"


def redact_text(text: str) -> str:
    text = _SESSION_TOKEN.sub("chm_" + REDACTED, text)
    text = _BEARER_VALUE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _PHONE.sub(_mask_phone, text)


def is_sensitive_key(key: str) -> bool:
    lowered = _CAMEL.sub("_", key).lower()
    if lowered in _SAFE_KEYS:
        return False
    if any(phrase in lowered for phrase in _SENSITIVE_PHRASES):
        return True
    return any(word in _SENSITIVE_WORDS for word in _WORD_SPLIT.split(lowered))


def redact_value(key: str, value: Any) -> Any:
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_value(key, v) for v in value]
    return value


def redact_processor(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        event_dict[key] = redact_value(key, event_dict[key])
    return event_dict


def _drop_color_message(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    event_dict.pop("color_message", None)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,  # correlation_id and other bound context
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.ExtraAdder(),
        _drop_color_message,
    ]


def configure_structlog() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_shared_processors(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def build_formatter_processors(*, json: bool) -> list[Processor]:
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    return [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.format_exc_info,
        # Redaction runs after exceptions are stringified, right before rendering.
        redact_processor,
        renderer,
    ]


def build_logging_config(*, level: str, json: bool) -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "foreign_pre_chain": _shared_processors(),
                "processors": build_formatter_processors(json=json),
            }
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "structured"},
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # One redacted line per request comes from CorrelationIdMiddleware instead.
            "django.server": {"level": "WARNING"},
            "django.request": {"level": "WARNING"},
            "django.db.backends": {"level": "WARNING"},
        },
    }


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


def bind_context(**values: Any) -> None:
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def current_context() -> MutableMapping[str, Any]:
    return structlog.contextvars.get_contextvars()
