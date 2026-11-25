"""Accounts HTTP endpoints: OTP login, tokens, devices, push tokens, PIN and step-up."""

from dataclasses import asdict
from typing import Any

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.devices import (
    end_all_sessions,
    end_session,
    list_devices,
    register_push_token,
    revoke_device,
    revoke_push_token,
)
from apps.accounts.otp import invalid_code, rate_limited, request_otp, verify_otp
from apps.accounts.pin import (
    cancel_reset,
    change_pin,
    complete_reset,
    request_reset,
    set_first_pin,
    verify_pin,
)
from apps.accounts.services import user_phone
from apps.accounts.sessions import DeviceInfo, SessionIdentity, login, mark_step_up, refresh
from apps.organizations.services import memberships_for_user, official_of_frozen_chama
from core.api_errors import ErrorSerializer
from core.client_ip import client_ip
from core.logging import get_logger
from core.ratelimit import Limit, RateLimitUnavailable, hit

log = get_logger(__name__)


class OtpRequestSerializer(serializers.Serializer[Any]):
    phone = serializers.CharField(max_length=32, help_text="Any accepted format, e.g. 0712345678.")
    device_id = serializers.RegexField(
        r"^[A-Za-z0-9_-]{8,64}$",
        required=False,
        help_text="The app installation's stable ID (used for per-device limits).",
    )


class OtpRequestedSerializer(serializers.Serializer[Any]):
    challenge_id = serializers.UUIDField()
    channel = serializers.ChoiceField(choices=["whatsapp", "sms"])
    expires_in = serializers.IntegerField(help_text="Seconds until the code expires.")
    resend_after = serializers.IntegerField(help_text="Seconds before another code may be sent.")


class OtpRequestView(APIView):
    """Send a one-time code to a phone number. The same response for every number."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="auth_otp_request",
        request=OtpRequestSerializer,
        responses={
            202: OtpRequestedSerializer,
            400: OpenApiResponse(ErrorSerializer, description="invalid_phone, validation_error"),
            429: OpenApiResponse(ErrorSerializer, description="rate_limited, locked"),
            503: OpenApiResponse(ErrorSerializer, description="service_unavailable"),
        },
    )
    def post(self, request: Request) -> Response:
        body = OtpRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        sent = request_otp(
            body.validated_data["phone"],
            ip=client_ip(request._request),
            device_id=body.validated_data.get("device_id"),
        )
        return Response(OtpRequestedSerializer(sent).data, status=status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------- login and sessions

_UNAUTHORIZED = OpenApiResponse(
    ErrorSerializer,
    description="invalid_token, token_expired, session_revoked, session_expired, refresh_reused",
)


class DeviceSerializer(serializers.Serializer[Any]):
    installation_id = serializers.RegexField(
        r"^[A-Za-z0-9_-]{8,64}$", help_text="The installation's stable ID, kept in secure storage."
    )
    platform = serializers.ChoiceField(choices=["android", "ios"])
    name = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")


class OtpVerifySerializer(serializers.Serializer[Any]):
    challenge_id = serializers.UUIDField()
    code = serializers.CharField(max_length=12)
    device = DeviceSerializer()


class TokenPairSerializer(serializers.Serializer[Any]):
    session_id = serializers.UUIDField()
    access_token = serializers.CharField()
    access_expires_in = serializers.IntegerField(help_text="Seconds.")
    refresh_token = serializers.CharField()
    refresh_expires_in = serializers.IntegerField(help_text="Seconds.")


class MembershipOutSerializer(serializers.Serializer[Any]):
    chama_id = serializers.UUIDField()
    role = serializers.CharField()
    status = serializers.CharField()


class LoginUserSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    is_new = serializers.BooleanField()


class LoginSerializer(TokenPairSerializer):
    user = LoginUserSerializer()
    active_chama_id = serializers.UUIDField(allow_null=True)
    memberships = MembershipOutSerializer(many=True)


class RefreshSerializer(serializers.Serializer[Any]):
    refresh_token = serializers.CharField(max_length=128)


def _throttle_refresh(request: Request) -> None:
    """Per-IP refresh limit (load protection). Fails open: refresh safety lives in Postgres."""
    ip = client_ip(request)
    if ip is None:
        return
    try:
        verdict = hit(Limit("refresh-ip", *settings.REFRESH_LIMIT_PER_IP), ip)
    except RateLimitUnavailable:
        log.warning("refresh_rate_limit_unavailable")
        return
    if not verdict.allowed:
        raise rate_limited(verdict.retry_after)


def _session(request: Request) -> SessionIdentity:
    """The verified session of an authenticated request (set by BearerSessionAuthentication)."""
    if not isinstance(request.auth, SessionIdentity):
        raise NotAuthenticated()
    return request.auth


class OtpVerifyView(APIView):
    """Verify the code, bind the device, start a session and return its tokens."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="auth_otp_verify",
        request=OtpVerifySerializer,
        responses={
            200: LoginSerializer,
            400: OpenApiResponse(ErrorSerializer, description="invalid_code, validation_error"),
            429: OpenApiResponse(ErrorSerializer, description="locked"),
        },
    )
    def post(self, request: Request) -> Response:
        body = OtpVerifySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        verified = verify_otp(data["challenge_id"], data["code"])
        memberships = memberships_for_user(verified.user_id)
        active = [m.chama_id for m in memberships if m.status == "active"]
        pair = login(
            verified.user_id,
            DeviceInfo(**data["device"]),
            active_chama_id=active[0] if len(active) == 1 else None,
        )
        return Response(
            LoginSerializer(
                {
                    **asdict(pair),
                    "user": {"id": verified.user_id, "is_new": verified.is_new_user},
                    "active_chama_id": active[0] if len(active) == 1 else None,
                    "memberships": [asdict(m) for m in memberships],
                }
            ).data
        )


class TokenRefreshView(APIView):
    """Rotate the refresh token. The previous token, within the grace window, gets the same pair."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="auth_token_refresh",
        request=RefreshSerializer,
        responses={
            200: TokenPairSerializer,
            401: _UNAUTHORIZED,
            429: OpenApiResponse(ErrorSerializer, description="rate_limited (per IP)"),
        },
    )
    def post(self, request: Request) -> Response:
        _throttle_refresh(request)
        body = RefreshSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(
            TokenPairSerializer(asdict(refresh(body.validated_data["refresh_token"]))).data
        )


class LogoutView(APIView):
    """End this session."""

    @extend_schema(
        operation_id="auth_logout", request=None, responses={204: None, 401: _UNAUTHORIZED}
    )
    def post(self, request: Request) -> Response:
        end_session(_session(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    """End every session of this user, on every device."""

    @extend_schema(
        operation_id="auth_logout_all", request=None, responses={204: None, 401: _UNAUTHORIZED}
    )
    def post(self, request: Request) -> Response:
        end_all_sessions(_session(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


_NOT_FOUND = OpenApiResponse(ErrorSerializer, description="not_found")


class DeviceOutSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    platform = serializers.CharField()
    created_at = serializers.DateTimeField()
    last_seen_at = serializers.DateTimeField()
    current = serializers.BooleanField(help_text="The device this request comes from.")


class DeviceListSerializer(serializers.Serializer[Any]):
    devices = DeviceOutSerializer(many=True)


class DevicesView(APIView):
    """The caller's devices (not revoked), most recently seen first."""

    @extend_schema(
        operation_id="auth_devices_list",
        responses={200: DeviceListSerializer, 401: _UNAUTHORIZED},
    )
    def get(self, request: Request) -> Response:
        devices = list_devices(_session(request))
        return Response(DeviceListSerializer({"devices": [asdict(d) for d in devices]}).data)


class DeviceView(APIView):
    """Revoke one of the caller's devices: its sessions end and its push token is revoked."""

    @extend_schema(
        operation_id="auth_devices_revoke",
        request=None,
        responses={204: None, 401: _UNAUTHORIZED, 404: _NOT_FOUND},
    )
    def delete(self, request: Request, device_id: Any) -> Response:
        revoke_device(_session(request), device_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PushTokenSerializer(serializers.Serializer[Any]):
    token_type = serializers.ChoiceField(choices=["expo", "fcm"])
    token = serializers.RegexField(
        r"^[!-~]{1,255}$",
        help_text="The push token as the platform gives it (printable ASCII, at most 255).",
    )


class PushTokenView(APIView):
    """This device's push token. Stored only; nothing is sent yet."""

    @extend_schema(
        operation_id="auth_push_token_set",
        request=PushTokenSerializer,
        responses={
            204: None,
            400: OpenApiResponse(ErrorSerializer, description="validation_error"),
            401: _UNAUTHORIZED,
        },
    )
    def put(self, request: Request) -> Response:
        identity = _session(request)
        body = PushTokenSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        register_push_token(identity, data["token_type"], data["token"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        operation_id="auth_push_token_delete",
        request=None,
        responses={204: None, 401: _UNAUTHORIZED},
    )
    def delete(self, request: Request) -> Response:
        revoke_push_token(_session(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- step-up

_STEP_UP_ERRORS = OpenApiResponse(ErrorSerializer, description="step_up_required, pin_not_set")


class StepUpOtpVerifySerializer(serializers.Serializer[Any]):
    challenge_id = serializers.UUIDField()
    code = serializers.CharField(max_length=12)


class FreshFactorSerializer(serializers.Serializer[Any]):
    factor = serializers.ChoiceField(choices=["otp", "pin"])
    fresh_for = serializers.IntegerField(help_text="Seconds this factor counts as fresh.")


class StepUpOtpRequestView(APIView):
    """Send a code to the caller's own number for step-up (same limits as login codes)."""

    @extend_schema(
        operation_id="auth_step_up_otp_request",
        request=None,
        responses={
            202: OtpRequestedSerializer,
            401: _UNAUTHORIZED,
            429: OpenApiResponse(ErrorSerializer, description="rate_limited, locked"),
            503: OpenApiResponse(ErrorSerializer, description="service_unavailable"),
        },
    )
    def post(self, request: Request) -> Response:
        identity = _session(request)
        sent = request_otp(user_phone(identity.user_id), ip=client_ip(request._request))
        return Response(OtpRequestedSerializer(sent).data, status=status.HTTP_202_ACCEPTED)


class StepUpOtpVerifyView(APIView):
    """Verify the step-up code: this session's OTP factor is fresh for 5 minutes."""

    @extend_schema(
        operation_id="auth_step_up_otp_verify",
        request=StepUpOtpVerifySerializer,
        responses={
            200: FreshFactorSerializer,
            400: OpenApiResponse(ErrorSerializer, description="invalid_code, validation_error"),
            401: _UNAUTHORIZED,
            429: OpenApiResponse(ErrorSerializer, description="locked"),
        },
    )
    def post(self, request: Request) -> Response:
        identity = _session(request)
        body = StepUpOtpVerifySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        verified = verify_otp(body.validated_data["challenge_id"], body.validated_data["code"])
        if verified.user_id != identity.user_id:  # a code for another number proves nothing
            raise invalid_code()
        mark_step_up(identity, "otp", timezone.now())
        fresh_for = settings.STEP_UP_OTP_FRESH_SECONDS
        return Response(FreshFactorSerializer({"factor": "otp", "fresh_for": fresh_for}).data)


class PinSerializer(serializers.Serializer[Any]):
    pin = serializers.CharField(max_length=12)


class PinChangeSerializer(serializers.Serializer[Any]):
    current_pin = serializers.CharField(max_length=12)
    new_pin = serializers.CharField(max_length=12)


class PinView(APIView):
    """Set the first server PIN (needs a fresh OTP on this session)."""

    @extend_schema(
        operation_id="auth_pin_set",
        request=PinSerializer,
        responses={
            204: None,
            400: OpenApiResponse(ErrorSerializer, description="pin_rejected, validation_error"),
            401: _UNAUTHORIZED,
            403: OpenApiResponse(ErrorSerializer, description="step_up_required"),
            409: OpenApiResponse(ErrorSerializer, description="pin_already_set"),
        },
    )
    def put(self, request: Request) -> Response:
        identity = _session(request)
        body = PinSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        set_first_pin(identity, body.validated_data["pin"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PinChangeView(APIView):
    """Change the server PIN; needs the current one."""

    @extend_schema(
        operation_id="auth_pin_change",
        request=PinChangeSerializer,
        responses={
            204: None,
            400: OpenApiResponse(ErrorSerializer, description="invalid_pin, pin_rejected"),
            401: _UNAUTHORIZED,
            403: _STEP_UP_ERRORS,
            429: OpenApiResponse(ErrorSerializer, description="pin_locked, rate_limited"),
            503: OpenApiResponse(ErrorSerializer, description="service_unavailable"),
        },
    )
    def post(self, request: Request) -> Response:
        identity = _session(request)
        body = PinChangeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        change_pin(identity, data["current_pin"], data["new_pin"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class StepUpPinView(APIView):
    """Verify the server PIN: this session's PIN factor is fresh for 15 minutes."""

    @extend_schema(
        operation_id="auth_step_up_pin",
        request=PinSerializer,
        responses={
            200: FreshFactorSerializer,
            400: OpenApiResponse(ErrorSerializer, description="invalid_pin"),
            401: _UNAUTHORIZED,
            403: _STEP_UP_ERRORS,
            429: OpenApiResponse(ErrorSerializer, description="pin_locked, rate_limited"),
            503: OpenApiResponse(ErrorSerializer, description="service_unavailable"),
        },
    )
    def post(self, request: Request) -> Response:
        identity = _session(request)
        body = PinSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        verify_pin(identity, body.validated_data["pin"])
        fresh_for = settings.STEP_UP_PIN_FRESH_SECONDS
        return Response(FreshFactorSerializer({"factor": "pin", "fresh_for": fresh_for}).data)


# ---------------------------------------------------------------- cooling-off and freeze (6b)


class PinResetSerializer(serializers.Serializer[Any]):
    effective_at = serializers.DateTimeField(help_text="Can be completed from this time.")
    expires_at = serializers.DateTimeField(help_text="Must be completed before this time.")


_NO_RESET = OpenApiResponse(ErrorSerializer, description="not_found (no reset is waiting)")


class PinForgotView(APIView):
    """Forgot PIN: request a new PIN. Needs a fresh step-up OTP; completes only after the
    cooling-off. The current PIN keeps working; other devices are alerted."""

    chama_context = False

    @extend_schema(
        operation_id="auth_pin_forgot",
        request=PinSerializer,
        responses={
            202: PinResetSerializer,
            400: OpenApiResponse(ErrorSerializer, description="pin_rejected, validation_error"),
            401: _UNAUTHORIZED,
            403: OpenApiResponse(
                ErrorSerializer, description="step_up_required, pin_not_set, cooling_off"
            ),
        },
    )
    def post(self, request: Request) -> Response:
        body = PinSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        effective, expires = request_reset(_session(request), body.validated_data["pin"])
        data = {"effective_at": effective, "expires_at": expires}
        return Response(PinResetSerializer(data).data, status=status.HTTP_202_ACCEPTED)


class PinForgotCompleteView(APIView):
    """Complete the reset after the cooling-off, with a fresh step-up OTP."""

    chama_context = False

    @extend_schema(
        operation_id="auth_pin_forgot_complete",
        request=None,
        responses={
            204: None,
            401: _UNAUTHORIZED,
            403: OpenApiResponse(
                ErrorSerializer, description="step_up_required, cooling_off, chama_frozen"
            ),
            404: _NO_RESET,
        },
    )
    def post(self, request: Request) -> Response:
        identity = _session(request)
        complete_reset(identity, lambda: official_of_frozen_chama(identity.user_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PinForgotCancelView(APIView):
    """Cancel the waiting reset from any signed-in device; no step-up."""

    chama_context = False

    @extend_schema(
        operation_id="auth_pin_forgot_cancel",
        request=None,
        responses={204: None, 401: _UNAUTHORIZED, 404: _NO_RESET},
    )
    def post(self, request: Request) -> Response:
        cancel_reset(_session(request))
        return Response(status=status.HTTP_204_NO_CONTENT)


