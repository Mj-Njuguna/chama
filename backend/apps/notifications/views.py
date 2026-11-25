"""Dev-only views. Routed only when ``FAKE_OTP_OUTBOX_ENABLED`` is true, which only dev
and test settings set; production neither routes nor installs them."""

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from apps.notifications.fake import outbox
from core.phones import InvalidPhone, normalize_phone


@require_GET
def dev_otp_outbox(request: HttpRequest) -> JsonResponse:
    """``GET /dev/otp-outbox?phone=0712345678``: codes "sent" to that number, newest first."""
    if not settings.FAKE_OTP_OUTBOX_ENABLED:  # defence in depth: the route is not even added
        raise Http404
    try:
        phone = normalize_phone(request.GET.get("phone", ""))
    except InvalidPhone as exc:
        return JsonResponse({"error": {"code": "invalid_phone", "reason": exc.code}}, status=400)
    return JsonResponse({"phone": phone, "messages": outbox(phone)})
