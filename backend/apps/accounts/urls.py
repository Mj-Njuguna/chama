"""Accounts routes, under /api/v1/auth/."""

from django.urls import path

from apps.accounts.views import (
    DevicesView,
    DeviceView,
    LogoutAllView,
    LogoutView,
    OtpRequestView,
    OtpVerifyView,
    PinChangeView,
    PinForgotCancelView,
    PinForgotCompleteView,
    PinForgotView,
    PinView,
    PushTokenView,
    StepUpOtpRequestView,
    StepUpOtpVerifyView,
    StepUpPinView,
    TokenRefreshView,
)

urlpatterns = [
    path("otp/request", OtpRequestView.as_view(), name="otp-request"),
    path("otp/verify", OtpVerifyView.as_view(), name="otp-verify"),
    path("token/refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("logout-all", LogoutAllView.as_view(), name="logout-all"),
    path("devices", DevicesView.as_view(), name="devices"),
    path("devices/<uuid:device_id>", DeviceView.as_view(), name="device"),
    path("push-token", PushTokenView.as_view(), name="push-token"),
    path("pin", PinView.as_view(), name="pin"),
    path("pin/change", PinChangeView.as_view(), name="pin-change"),
    path("pin/forgot", PinForgotView.as_view(), name="pin-forgot"),
    path("pin/forgot/complete", PinForgotCompleteView.as_view(), name="pin-forgot-complete"),
    path("pin/forgot/cancel", PinForgotCancelView.as_view(), name="pin-forgot-cancel"),
    path("step-up/pin", StepUpPinView.as_view(), name="step-up-pin"),
    path("step-up/otp/request", StepUpOtpRequestView.as_view(), name="step-up-otp-request"),
    path("step-up/otp/verify", StepUpOtpVerifyView.as_view(), name="step-up-otp-verify"),
]
