"""Celery application. Tasks are autodiscovered from each app's ``tasks`` module."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("chama")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Correlation-ID propagation across the broker (see core.logging).
import core.celery_signals  # noqa: E402, F401
