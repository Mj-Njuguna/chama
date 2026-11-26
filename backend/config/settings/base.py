"""Settings shared by every environment. Environment-specific overrides live in dev/test/prod."""

from pathlib import Path

from celery.schedules import crontab
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

# django.contrib.admin and sessions are absent: no password or cookie logins.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",  # for AUTH_USER_MODEL only: no password logins, no permissions
    "core",  # model-free shared kernel; registers the tenancy system checks
    "rest_framework",
    "drf_spectacular",
    *DOMAIN_APPS,
]

# accounts.User is the user model. No authentication backends, so Django can never
# authenticate anyone by password; logins are OTP-based with opaque session tokens.
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS: list[str] = []

MIDDLEWARE = [
    "core.middleware.CorrelationIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Authentication sets request.authenticated_chama_id from the session; the chama context
    # middleware must come after it (enforced by core.checks, tenancy.E003).
    "apps.accounts.middleware.SessionAuthenticationMiddleware",
    "core.middleware.ChamaContextMiddleware",
]
TENANCY_AUTHENTICATION_MIDDLEWARE = "apps.accounts.middleware.SessionAuthenticationMiddleware"

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

# ---------------------------------------------------------------- Step-up
# Argon2id cost for the server PIN. A 6-digit PIN has only a million values, so the pepper and
# the attempt limits protect it, not this cost; parameters are stored on each hash and a hash
# is upgraded on the next successful check when these change.
PIN_ARGON2_TIME_COST = env_int("PIN_ARGON2_TIME_COST", 3)
PIN_ARGON2_MEMORY_KIB = env_int("PIN_ARGON2_MEMORY_KIB", 64 * 1024)
PIN_ARGON2_PARALLELISM = env_int("PIN_ARGON2_PARALLELISM", 1)
# Wrong PINs before a lock; then an escalating lockout and a fresh OTP to continue.
PIN_MAX_FAILED_ATTEMPTS = 5
PIN_LOCKOUT_STEPS_SECONDS = (900, 3600, 86400)
PIN_LOCKOUT_RESET_HOURS = 24  # the level resets after this long without a lockout
# Wrong PINs per user per Nairobi day, across unlocks; then locked until midnight.
PIN_DAILY_FAILURE_CAP = 10
# PIN checks per session per window (limit, seconds), before any hashing (Redis).
PIN_VERIFY_LIMIT_PER_SESSION = (10, 900)
# How long a verified factor counts as fresh for step-up on the same session.
STEP_UP_PIN_FRESH_SECONDS = 15 * 60
STEP_UP_OTP_FRESH_SECONDS = 5 * 60
# Cooling-off for payment linking (24-72 h), and how long after that an official has to apply
# the change before it expires.
CHAMA_CHANGE_COOLING_OFF_HOURS = 48
PENDING_CHANGE_EXPIRY_HOURS = 7 * 24
# Forgot PIN: the new PIN can be completed only after this (then within the expiry above).
PIN_RESET_COOLING_OFF_HOURS = 48
# A device first seen this recently may not do the highest-risk actions, unless the chama
# itself is younger than this.
NEW_DEVICE_WINDOW_HOURS = 48

# ---------------------------------------------------------------- Sessions
ACCESS_TOKEN_TTL_SECONDS = env_int("ACCESS_TOKEN_TTL_SECONDS", 900)
REFRESH_TOKEN_TTL_SECONDS = env_int("REFRESH_TOKEN_TTL_SECONDS", 30 * 86400)
# Absolute limit on a session, however often it is refreshed; then a fresh OTP is needed.
SESSION_MAX_AGE_SECONDS = env_int("SESSION_MAX_AGE_SECONDS", 90 * 86400)
# The previous refresh token re-returns the same successor pair within this window.
REFRESH_GRACE_SECONDS = env_int("REFRESH_GRACE_SECONDS", 10)
# Refreshes per client IP per window: load protection only (carrier NAT puts many users behind
# one IP). Fails open if Redis is down.
REFRESH_LIMIT_PER_IP = (300, 60)

# ---------------------------------------------------------------- One-time codes
OTP_LENGTH = 6
OTP_TTL_SECONDS = env_int("OTP_TTL_SECONDS", 300)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
OTP_RESEND_AFTER_SECONDS = env_int("OTP_RESEND_AFTER_SECONDS", 60)
# Number lockout after a challenge is locked: 15 min, 1 h, 24 h; the level resets after a quiet
# OTP_LOCKOUT_RESET_SECONDS since the last lockout.
OTP_LOCKOUT_STEPS_SECONDS = [900, 3600, 86400]
OTP_LOCKOUT_RESET_SECONDS = env_int("OTP_LOCKOUT_RESET_SECONDS", 86400)
# Fixed-window limits in Redis as (hits, window seconds). Per number is the primary control.
# Per IP is deliberately loose: mobile carrier NAT puts many users behind one IP.
OTP_LIMIT_PER_NUMBER = (3, 900)
OTP_LIMIT_PER_IP = (60, 3600)
OTP_LIMIT_PER_DEVICE = (5, 3600)
# Codes per number per day (Africa/Nairobi), counted in Postgres.
OTP_DAILY_CAP_PER_NUMBER = env_int("OTP_DAILY_CAP_PER_NUMBER", 10)
# Estimated cost per accepted code, in KES minor units. 0 until provider prices are confirmed.
OTP_ESTIMATED_COST_MINOR = {"whatsapp": 0, "sms": 0}
# All-numbers sends per day above which an alert fires once (OTP-pumping defence). Max 9999.
OTP_DAILY_SEND_ALERT_THRESHOLD = env_int("OTP_DAILY_SEND_ALERT_THRESHOLD", 1000)
# Challenges are deleted after this (longer than the daily-cap window).
OTP_CHALLENGE_RETENTION_HOURS = 48

# Chamas one user may create per Nairobi day (POST /chamas), counted in Postgres.
CHAMAS_PER_USER_PER_DAY = env_int("CHAMAS_PER_USER_PER_DAY", 3)
# Wording version of the invite terms a member accepts when joining.
INVITE_TERMS_VERSION = "2026-11-25"

# Proxies in front of the app that append X-Forwarded-For (core.client_ip). Production must set
# it explicitly.
TRUSTED_PROXY_COUNT = env_int("TRUSTED_PROXY_COUNT", 0)

# ---------------------------------------------------------------- Notifications
# OTP senders per channel (dotted path to a zero-argument factory). Real providers are added once
# their APIs are verified; until then production configures none, so OTP requests fail closed.
OTP_SENDERS: dict[str, str] = {}
# Invitation sender (dotted path). Empty: invitations are seen in the app only.
INVITE_SENDER = ""
# Member message senders per channel (dotted paths), tried WhatsApp first, SMS on undeliverable.
# Empty: messages are recorded as skipped. WhatsApp Cloud: "apps.notifications.whatsapp.
# WhatsAppCloudSender" plus the WHATSAPP_* values below. No SMS provider is chosen yet.
MESSAGE_SENDERS: dict[str, str] = {}
WHATSAPP_API_VERSION = env_str("WHATSAPP_API_VERSION", "v27.0")
WHATSAPP_PHONE_NUMBER_ID = env_str("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN = env_str("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_TEMPLATE_LANGUAGE = env_str("WHATSAPP_TEMPLATE_LANGUAGE", "en")
WHATSAPP_APP_SECRET = env_str("WHATSAPP_APP_SECRET", "")  # webhook signature (X-Hub-Signature-256)
WHATSAPP_VERIFY_TOKEN = env_str("WHATSAPP_VERIFY_TOKEN", "")  # webhook subscription check
# Hard wall-clock limit on every provider call.
MESSAGING_PROVIDER_TIMEOUT_SECONDS = 5
# Fakes and the dev OTP outbox: dev and test only (notifications.E001-E004).
MESSAGING_ALLOW_FAKE = False
FAKE_OTP_OUTBOX_ENABLED = False
FAKE_OTP_OUTBOX_TTL_SECONDS = 600
# Push provider (dotted path to a zero-argument factory). None until Expo/FCM details are
# verified.
PUSH_PROVIDER = ""
# SIM-swap / number-verification signals: interface only until an operator API is confirmed.
# Escalates verification, never proof.
OPERATOR_SIGNAL_PROVIDER = "apps.accounts.operator_signals.NoOpOperatorSignalProvider"

LANGUAGE_CODE = "en"
USE_I18N = False

# ---------------------------------------------------------------- Payments (M-Pesa)
# Dotted path to a zero-argument M-Pesa gateway factory. Empty: payments unavailable (linking can
# still be saved; STK requests answer 503). The Daraja adapter is UNVERIFIED and is never a
# default (apps/payments/daraja.py).
MPESA_GATEWAY = env_str("MPESA_GATEWAY", "")
# Public HTTPS origin the provider calls back (no trailing slash), e.g. https://api.example.com.
PAYMENTS_CALLBACK_BASE_URL = env_str("PAYMENTS_CALLBACK_BASE_URL", "http://localhost:8001")
# A pending STK prompt older than this is checked once, then marked timeout.
STK_TIMEOUT_SECONDS = 180
# Development only: the fake gateway answers STK prompts with success after this many seconds.
FAKE_MPESA_AUTOCOMPLETE_SECONDS = 0

# ---------------------------------------------------------------- DRF / OpenAPI
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # Bearer session tokens: identity comes from SessionAuthenticationMiddleware.
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.BearerSessionAuthentication"],
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
        "RoleEnum": "apps.organizations.roles.Role",
        "MembershipStatusEnum": "apps.organizations.models.MembershipStatus",
        "PendingChangeStatusEnum": "apps.organizations.models.PendingChangeStatus",
        "ReceiptChannelEnum": "apps.ledger.models.ReceiptChannel",
        "LinkingChannelEnum": "apps.payments.models.LinkingChannel",
        "LinkingEnvironmentEnum": "apps.payments.models.LinkingEnvironment",
        "StkStatusEnum": "apps.payments.models.StkStatus",
        "LinkingStateEnum": "apps.payments.views.LINKING_STATES",
        "PaymentStateEnum": "apps.payments.views.PAYMENT_STATES",
        "MemberCycleStatusEnum": "apps.reports.views.CYCLE_STATUSES",
        "CycleStatusEnum": "apps.contributions.models.CycleStatus",
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
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    "accounts-purge-otp-records": {
        "task": "apps.accounts.tasks.purge_otp_records",
        "schedule": 3600.0,
    },
    "payments-sweep-stk-timeouts": {
        "task": "apps.payments.tasks.sweep_stk_timeouts",
        "schedule": 60.0,
    },
    # Contributions. Africa/Nairobi is UTC+3 with no daylight saving; times below are UTC.
    # 00:05 EAT: late fines for contributions whose grace ended yesterday.
    "contributions-assess-fines": {
        "task": "apps.contributions.tasks.dispatch",
        "schedule": crontab(hour=21, minute=5),
        "args": ("assess_fines",),
    },
    # 00:30 EAT: cycles due within a week, and every active member's obligations.
    "contributions-generate-cycles": {
        "task": "apps.contributions.tasks.dispatch",
        "schedule": crontab(hour=21, minute=30),
        "args": ("generate_cycles",),
    },
    # 07:00 EAT: due-today reminders and the opt-in M-Pesa prompts.
    "contributions-due-reminders": {
        "task": "apps.contributions.tasks.dispatch",
        "schedule": crontab(hour=4, minute=0),
        "args": ("due_reminders",),
    },
    # 08:00 EAT: reminders three days before the due date.
    "contributions-pre-reminders": {
        "task": "apps.contributions.tasks.dispatch",
        "schedule": crontab(hour=5, minute=0),
        "args": ("pre_reminders",),
    },
}
# Spread per-chama jobs over this many seconds (month-start bursts).
CONTRIBUTIONS_JOB_JITTER_SECONDS = 15 * 60
CELERY_BEAT_SCHEDULE_FILENAME = env_str("CELERY_BEAT_SCHEDULE_FILENAME", "/tmp/celerybeat-schedule")  # noqa: S108
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# ---------------------------------------------------------------- Logging (structlog, JSON)
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")
LOG_JSON = env_str("LOG_FORMAT", "json") != "console"
configure_structlog()
LOGGING = build_logging_config(level=LOG_LEVEL, json=LOG_JSON)  # applied by Django at setup
