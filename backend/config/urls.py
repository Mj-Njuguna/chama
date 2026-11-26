from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path

from core.health import healthz, readyz

api_v1: list[URLPattern | URLResolver] = [  # each app adds its routes here
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.organizations.urls")),
    path("", include("apps.ledger.urls")),  # /chama/receipts
    path("", include("apps.reports.urls")),  # /chama/home
    path("", include("apps.contributions.urls")),  # cycles, obligations, fines, cashbook
    path("", include("apps.notifications.urls")),  # feed, WhatsApp webhook
]

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("api/v1/", include((api_v1, "api"), namespace="v1")),
]

# Dev-only routes: never added in production.
if settings.FAKE_OTP_OUTBOX_ENABLED:
    from apps.notifications.views import dev_otp_outbox

    urlpatterns.append(path("dev/otp-outbox", dev_otp_outbox, name="dev-otp-outbox"))
