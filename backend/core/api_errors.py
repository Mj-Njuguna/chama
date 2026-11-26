"""One error shape for every API error:

    {"error": {"code": "rate_limited", "message": "...", "retry_after": 60}}

``code`` is a stable machine-readable string the mobile app switches on; ``message`` is for
developers and may change. Optional fields: ``retry_after`` (seconds), ``field_errors``
(validation errors per field) and ``factors`` (step-up).

Raise :class:`ApiError` (or a subclass) for domain errors. DRF's own exceptions and Django's
``Http404``/``PermissionDenied`` are mapped to the same shape. Never put a phone number, code or
token in ``message``.
"""

from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, serializers, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from core.phones import PhoneError


class ApiError(exceptions.APIException):
    """A domain error with a stable ``code``. Extra keyword arguments become response fields."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    default_code = "bad_request"
    default_detail = "The request could not be processed."

    def __init__(
        self,
        code: str | None = None,
        message: str | None = None,
        *,
        status_code: int | None = None,
        **extra: Any,
    ) -> None:
        self.error_code = code or self.default_code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra
        super().__init__(detail=message or self.default_detail, code=self.error_code)


def chama_frozen() -> ApiError:
    """403: the chama is frozen ("this wasn't me") until an operator reviews it."""
    return ApiError(
        "chama_frozen",
        "This chama is frozen until it is reviewed.",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def cooling_off(retry_after: int) -> ApiError:
    """403: the change is waiting out its safety period."""
    return ApiError(
        "cooling_off",
        "This change is waiting out its safety period.",
        status_code=status.HTTP_403_FORBIDDEN,
        retry_after=retry_after,
    )


# DRF exception class -> stable code. Anything else uses the exception's own default_code.
_CODES: dict[type[exceptions.APIException], str] = {
    exceptions.ValidationError: "validation_error",
    exceptions.ParseError: "malformed_request",
    exceptions.NotAuthenticated: "not_authenticated",
    exceptions.AuthenticationFailed: "not_authenticated",
    exceptions.PermissionDenied: "permission_denied",
    exceptions.NotFound: "not_found",
    exceptions.MethodNotAllowed: "method_not_allowed",
    exceptions.UnsupportedMediaType: "unsupported_media_type",
    exceptions.Throttled: "rate_limited",
}


def state_conflict(reason: str, message: str) -> ApiError:
    """409: the action conflicts with the current state; ``reason`` is in STATE_CONFLICTS."""
    if reason not in STATE_CONFLICTS:
        raise ValueError(f"unknown state_conflict reason {reason!r}")
    return ApiError("state_conflict", message, status_code=409, reason=reason)


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, exceptions.APIException):
        return response  # unhandled: a 500, logged by Django
    extra: dict[str, Any] = {}
    if isinstance(exc, ApiError):
        code, message, extra = exc.error_code, str(exc.detail), dict(exc.extra)
    elif isinstance(exc, exceptions.ValidationError):
        code, message = "validation_error", "Some fields are invalid."
        extra["field_errors"] = exc.detail
    else:
        code = next((c for cls, c in _CODES.items() if isinstance(exc, cls)), str(exc.default_code))
        message = str(exc.detail)
        wait = getattr(exc, "wait", None)  # Throttled only
        if wait is not None:
            extra["retry_after"] = int(wait)
    response.data = error_body(code, message, **extra)
    if response.status_code == status.HTTP_401_UNAUTHORIZED and "WWW-Authenticate" not in response:
        response["WWW-Authenticate"] = 'Bearer realm="api"'
    return response


# ---------------------------------------------------------------- OpenAPI components

# Every ``code`` the API can return. The
# schema publishes it as an enum, so the generated client can switch on it exhaustively.
# tests/test_error_codes.py keeps it in sync with the code and the contract docs. A new code is
# added here, in the docs and in the code together.
ERROR_CODES = (
    # generic (errors.md)
    "validation_error",
    "malformed_request",
    "not_authenticated",
    "permission_denied",
    "not_found",
    "method_not_allowed",
    "unsupported_media_type",
    "rate_limited",
    # one-time codes
    "invalid_phone",
    "invalid_code",
    "locked",
    "service_unavailable",
    # sessions
    "invalid_token",
    "token_expired",
    "session_revoked",
    "session_expired",
    "refresh_reused",
    # chamas
    "chama_not_available",
    # step-up
    "step_up_required",
    "cooling_off",
    "chama_frozen",
    "pin_not_set",
    "pin_already_set",
    "pin_rejected",
    "invalid_pin",
    "pin_locked",
    # domain state
    "state_conflict",
)
# Why a new PIN was refused (pin_rejected).
PIN_REJECTIONS = ("not_six_digits", "repeated", "sequence", "phone_digits")
# Why an action conflicts with the current state (state_conflict, 409). Each module adds its own.
STATE_CONFLICTS: tuple[str, ...] = (
    "duplicate_receipt",
    "member_not_active",
    # fines and waivers
    "fine_not_open",
    "waiver_pending",
    "waiver_decided",
    "same_official",
)
# ``reason`` sub-codes: those of invalid_phone, pin_rejected and state_conflict.
ERROR_REASONS = (*(reason.value for reason in PhoneError), *PIN_REJECTIONS, *STATE_CONFLICTS)
# Factors a step_up_required answer may list.
STEP_UP_FACTORS = ("otp", "pin")


class ErrorDetailSerializer(serializers.Serializer[Any]):
    code = serializers.ChoiceField(choices=ERROR_CODES, help_text="Switch on this.")
    message = serializers.CharField(help_text="For logs only; never parse or show it.")
    retry_after = serializers.IntegerField(required=False, help_text="Seconds to wait.")
    reason = serializers.ChoiceField(
        choices=ERROR_REASONS,
        required=False,
        help_text="Sub-code of invalid_phone, pin_rejected, state_conflict.",
    )
    attempts_left = serializers.IntegerField(required=False, help_text="With invalid_pin.")
    factors = serializers.ListField(
        child=serializers.ChoiceField(choices=STEP_UP_FACTORS),
        required=False,
        help_text="With step_up_required: verify each, in order, then retry.",
    )
    field_errors = serializers.DictField(required=False, help_text="Validation errors per field.")


class ErrorSerializer(serializers.Serializer[Any]):
    """The body of every error response."""

    error = ErrorDetailSerializer()
