"""pytest settings. No real network calls: every provider is a fake."""

from config.settings.base import *  # noqa: F403
from config.settings.base import REDIS_URL

SECRET_KEY = "test-only-key"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# Tests get their own Redis database.
REDIS_URL = REDIS_URL.rsplit("/", 1)[0] + "/15"
