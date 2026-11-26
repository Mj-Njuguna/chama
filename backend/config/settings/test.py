"""pytest settings. No real network calls: every provider is a fake."""

from config.settings.base import *  # noqa: F403
from config.settings.base import INSTALLED_APPS, REDIS_URL

SECRET_KEY = "test-only-key"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# Tests get their own Redis database.
REDIS_URL = REDIS_URL.rsplit("/", 1)[0] + "/15"

# Fakes only (no network): codes go to the outbox at GET /dev/otp-outbox?phone=...
MESSAGING_ALLOW_FAKE = True
FAKE_OTP_OUTBOX_ENABLED = True
OTP_SENDERS = {
    "whatsapp": "apps.notifications.fake.FakeWhatsAppSender",
    "sms": "apps.notifications.fake.FakeSmsSender",
}
INVITE_SENDER = "apps.notifications.invites.FakeInviteSender"
PUSH_PROVIDER = "apps.notifications.fake.FakePushProvider"

# Test-only app with one chama-scoped table, to prove RLS independently of real chama tables.
INSTALLED_APPS = [*INSTALLED_APPS, "tests.rls_probe"]
# Cheap Argon2 in tests (the cost is a setting; production keeps ~64 MiB, 3 passes).
PIN_ARGON2_TIME_COST = 1
PIN_ARGON2_MEMORY_KIB = 8 * 1024

# Fake M-Pesa gateway (no network). Never in production (payments.E001).
MPESA_GATEWAY = "apps.payments.fake.FakeMpesaGateway"
