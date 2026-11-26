"""Notifications storage. Generated, then edited: ``chama_rls`` on messages and feed events;
composite foreign key from messages to memberships; the feed is append-only; provider refs are
write-once (RLS-exempt, IDs only)."""

import uuid

import django.db.models.deletion
from django.db import migrations, models

from core.tenancy import chama_rls

FOREIGN_KEYS = """
ALTER TABLE notifications_message ADD CONSTRAINT notif_message_member_fk
    FOREIGN KEY (chama_id, member_id) REFERENCES organizations_membership (chama_id, id)
    ON DELETE RESTRICT;
"""
DROP_FOREIGN_KEYS = "ALTER TABLE notifications_message DROP CONSTRAINT notif_message_member_fk;"

GRANTS = """
REVOKE UPDATE, DELETE ON notifications_feedevent, notifications_providermessageref FROM app_rw;
REVOKE DELETE ON notifications_message FROM app_rw;
"""
REVERSE_GRANTS = """
GRANT UPDATE, DELETE ON notifications_feedevent, notifications_providermessageref TO app_rw;
GRANT DELETE ON notifications_message TO app_rw;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organizations", "0002_security"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderMessageRef",
            fields=[
                (
                    "provider_message_id",
                    models.CharField(max_length=128, primary_key=True, serialize=False),
                ),
                ("chama_id", models.UUIDField()),
                ("message_id", models.UUIDField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="FeedEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("kind", models.CharField(max_length=40)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("key", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["chama", "created_at"], name="notif_feed_created_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("chama", "key"), name="notif_feed_key_uniq")
                ],
            },
        ),
        migrations.CreateModel(
            name="Message",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("member_id", models.UUIDField()),
                ("template", models.CharField(max_length=40)),
                ("params", models.JSONField(blank=True, default=list)),
                ("idempotency_key", models.CharField(max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("delivered", "Delivered"),
                            ("read", "Read"),
                            ("undeliverable", "Undeliverable"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("channel", models.CharField(blank=True, default="", max_length=16)),
                ("provider_message_id", models.CharField(blank=True, default="", max_length=128)),
                ("error_code", models.CharField(blank=True, default="", max_length=32)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["chama", "status", "created_at"], name="notif_message_status_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("chama", "idempotency_key"), name="notif_message_key_uniq"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                [
                                    "queued",
                                    "sending",
                                    "sent",
                                    "delivered",
                                    "read",
                                    "undeliverable",
                                    "failed",
                                    "skipped",
                                ],
                            )
                        ),
                        name="notif_message_status_valid",
                    ),
                ],
            },
        ),
        chama_rls("notifications_message"),
        chama_rls("notifications_feedevent"),
        migrations.RunSQL(FOREIGN_KEYS, DROP_FOREIGN_KEYS),
        migrations.RunSQL(GRANTS, REVERSE_GRANTS),
    ]
