"""Production settings. Fails fast on missing configuration."""

import os

from django.core.exceptions import ImproperlyConfigured

from config.settings.base import *  # noqa: F403
from config.settings.base import ALLOWED_HOSTS, SECRET_KEY
from core.env import env_bool, env_int
from core.keys import parse_key_ring

DEBUG = False

if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set (50+ chars) in production")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production")

#: the number of trusted proxies decides which IP is the client's; never a default.
if "TRUSTED_PROXY_COUNT" not in os.environ:
    raise ImproperlyConfigured("TRUSTED_PROXY_COUNT must be set explicitly in production")

# Key rings: required from the environment, never the dev default.
for _ring in (
    "AUDIT_HASH_KEYS",
    "OTP_PEPPER_KEYS",
    "AUTH_TOKEN_KEYS",
    "PIN_PEPPER_KEYS",
    "FIELD_ENCRYPTION_KEYS",
):
    if not os.environ.get(_ring):
        raise ImproperlyConfigured(f"{_ring} must be set in production")
    parse_key_ring(_ring, os.environ[_ring])

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

SILENCED_SYSTEM_CHECKS = [
    # The API authenticates with bearer tokens, never cookies, so there is no CSRF surface.
    # Revisit if a cookie-based web dashboard is added (post-MVP).
    "security.W003",
    # HSTS preload is a decision about the production domain, not the code.
    "security.W021",
]
