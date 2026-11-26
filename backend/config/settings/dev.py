"""Local development (docker compose). Never used in production."""

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS
from core.env import env_str

DEBUG = True
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-only-insecure-key-not-for-production")
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1", "0.0.0.0", "app"]  # noqa: S104

# Fakes only (no network): codes go to the outbox at GET /dev/otp-outbox?phone=...
MESSAGING_ALLOW_FAKE = True
FAKE_OTP_OUTBOX_ENABLED = True
OTP_SENDERS = {
    "whatsapp": "apps.notifications.fake.FakeWhatsAppSender",
    "sms": "apps.notifications.fake.FakeSmsSender",
}
INVITE_SENDER = "apps.notifications.invites.FakeInviteSender"
PUSH_PROVIDER = "apps.notifications.fake.FakePushProvider"
MESSAGE_SENDERS = {
    "whatsapp": "apps.notifications.fake.FakeWhatsAppMessenger",
    "sms": "apps.notifications.fake.FakeSmsMessenger",
}
