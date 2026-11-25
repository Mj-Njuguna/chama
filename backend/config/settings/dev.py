"""Local development (docker compose). Never used in production."""

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS
from core.env import env_str

DEBUG = True
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-only-insecure-key-not-for-production")
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1", "0.0.0.0", "app"]  # noqa: S104
