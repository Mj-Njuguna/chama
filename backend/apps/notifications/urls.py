"""Notifications routes under /api/v1/."""

from django.urls import path

from apps.notifications.api import FeedView, whatsapp_webhook

urlpatterns = [
    path("chama/feed", FeedView.as_view(), name="chama-feed"),
    path("notifications/whatsapp/webhook", whatsapp_webhook, name="whatsapp-webhook"),
]
