"""Notifications storage. Internal: other modules use ``apps.notifications.services``.

- ``Message``: one outbound member message (WhatsApp first, SMS fallback), unique per chama on
  its idempotency key, so a reminder or receipt is never sent twice.
- ``FeedEvent``: the chama's in-app activity feed (append-only).
- ``ProviderMessageRef``: maps a provider message id (a WhatsApp ``wamid``) to its chama, so a
  status webhook, which arrives without any chama context, can find the message. IDs only.
"""

import uuid
from typing import ClassVar

from django.db import models


class MessageStatus(models.TextChoices):
    QUEUED = "queued"
    SENDING = "sending"  # claimed by a worker; the provider call is in progress
    SENT = "sent"  # accepted by the provider
    DELIVERED = "delivered"
    READ = "read"
    UNDELIVERABLE = "undeliverable"  # every channel refused or could not reach the member
    FAILED = "failed"
    SKIPPED = "skipped"  # no consent, no channel configured


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    member_id = models.UUIDField()
    template = models.CharField(max_length=40)
    params = models.JSONField(default=list, blank=True)  # positional template parameters
    idempotency_key = models.CharField(max_length=120)
    status = models.CharField(
        max_length=16, choices=MessageStatus.choices, default=MessageStatus.QUEUED
    )
    channel = models.CharField(max_length=16, blank=True, default="")
    provider_message_id = models.CharField(max_length=128, blank=True, default="")
    error_code = models.CharField(max_length=32, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["chama", "idempotency_key"], name="notif_message_key_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=MessageStatus.values),
                name="notif_message_status_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "status", "created_at"], name="notif_message_status_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class FeedEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=40)
    payload = models.JSONField(default=dict, blank=True)
    # Business event this entry is for: posting it again adds nothing.
    key = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["chama", "key"], name="notif_feed_key_uniq"),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama", "created_at"], name="notif_feed_created_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class ProviderMessageRef(models.Model):
    provider_message_id = models.CharField(max_length=128, primary_key=True)
    chama_id = models.UUIDField()
    message_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.provider_message_id
