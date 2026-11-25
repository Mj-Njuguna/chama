"""Settings shared by every environment. Environment-specific overrides live in dev/test/prod."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from core.env import env_int, env_list, env_str
from core.keys import dev_key_ring_value
from core.logging import build_logging_config, configure_structlog

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = env_str("DJANGO_SECRET_KEY", "")  # prod.py refuses to start without one
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

# The modules of the monolith. Cross-app calls go through services.py / events.py only.
DOMAIN_APPS = [
    "apps.accounts",
    "apps.organizations",
    "apps.payments",
    "apps.ledger",
    "apps.contributions",
    "apps.lending",
    "apps.notifications",
    "apps.reports",
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "core",  # model-free shared kernel
    "rest_framework",
    "drf_spectacular",
    *DOMAIN_APPS,
]

MIDDLEWARE = [
    "core.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES: list[dict[str, object]] = []

# Processes connect as app_rw (subject to row-level security, owns nothing). Only the migration
# step sets DB_CONNECT_AS=owner and supplies app_owner credentials.
DB_CONNECT_AS = env_str("DB_CONNECT_AS", "app")
if DB_CONNECT_AS not in {"app", "owner"}:
    raise ImproperlyConfigured("DB_CONNECT_AS must be 'app' or 'owner'")
_DB_OWNER = DB_CONNECT_AS == "owner"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env_str("POSTGRES_HOST", "localhost"),
        "PORT": env_int("POSTGRES_PORT", 5432),
        "NAME": env_str("POSTGRES_DB", "chama"),
        "USER": env_str("POSTGRES_OWNER_USER") if _DB_OWNER else env_str("POSTGRES_USER", "app_rw"),
        "PASSWORD": env_str("POSTGRES_OWNER_PASSWORD")
        if _DB_OWNER
        else env_str("POSTGRES_PASSWORD", ""),
        "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "ATOMIC_REQUESTS": False,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env_str("REDIS_URL", "redis://localhost:6379/0")

# Store UTC; business logic converts to BUSINESS_TIME_ZONE explicitly.
USE_TZ = True
TIME_ZONE = "UTC"
BUSINESS_TIME_ZONE = "Africa/Nairobi"

# Phone numbers are accepted only from these countries (ISO 3166 alpha-2) and only when mobile
# (core.phones). The first entry is the region for numbers written without "+".
ALLOWED_COUNTRY_CODES = ["KE"]

# ---------------------------------------------------------------- Secrets with key IDs
# Each is a key ring "id:base64url,..." (newest first), so keys rotate without a flag day. Dev and
# tests use fixed non-secret rings; prod settings refuse to start without the real value.
AUDIT_HASH_KEYS = env_str("AUDIT_HASH_KEYS", dev_key_ring_value("audit hash"))
OTP_PEPPER_KEYS = env_str("OTP_PEPPER_KEYS", dev_key_ring_value("otp pepper"))
AUTH_TOKEN_KEYS = env_str("AUTH_TOKEN_KEYS", dev_key_ring_value("auth token"))
PIN_PEPPER_KEYS = env_str("PIN_PEPPER_KEYS", dev_key_ring_value("pin pepper"))
FIELD_ENCRYPTION_KEYS = env_str("FIELD_ENCRYPTION_KEYS", dev_key_ring_value("field encryption"))

# ---------------------------------------------------------------- Lists
# Every list endpoint: cursor pagination with these page sizes; larger requests are capped.
LIST_DEFAULT_PAGE_SIZE = 50
LIST_MAX_PAGE_SIZE = 200
LIST_CURSOR_MAX_AGE_SECONDS = 24 * 3600

# Proxies in front of the app that append X-Forwarded-For (core.client_ip). Production must set
# it explicitly.
TRUSTED_PROXY_COUNT = env_int("TRUSTED_PROXY_COUNT", 0)

LANGUAGE_CODE = "en"
USE_I18N = False

# ---------------------------------------------------------------- DRF / OpenAPI
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
    # One error shape for every API error (core.api_errors).
    "EXCEPTION_HANDLER": "core.api_errors.exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Chama API",
    "DESCRIPTION": "API consumed by the Chama mobile app. The TypeScript client is generated "
    "from this schema.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "ErrorCodeEnum": "core.api_errors.ERROR_CODES",
        "ErrorReasonEnum": "core.api_errors.ERROR_REASONS",
        "StepUpFactorEnum": "core.api_errors.STEP_UP_FACTORS",
    },
}

# ---------------------------------------------------------------- Celery
CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {}
CELERY_BEAT_SCHEDULE_FILENAME = env_str("CELERY_BEAT_SCHEDULE_FILENAME", "/tmp/celerybeat-schedule")  # noqa: S108
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# ---------------------------------------------------------------- Logging (structlog, JSON)
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")
LOG_JSON = env_str("LOG_FORMAT", "json") != "console"
configure_structlog()
LOGGING = build_logging_config(level=LOG_LEVEL, json=LOG_JSON)  # applied by Django at setup
