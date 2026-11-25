"""Test-only probe tables, installed by test settings only, under chama RLS."""

import uuid

import django.db.models.deletion
from django.db import migrations, models

import core.crypto
from core.tenancy import chama_rls


class Migration(migrations.Migration):
    initial = True

    dependencies = [("organizations", "0002_security")]

    operations = [
        migrations.CreateModel(
            name="ProbeRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("name", models.CharField(max_length=64)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="organizations.chama"
                    ),
                ),
            ],
        ),
        chama_rls("rls_probe_proberecord"),
        migrations.CreateModel(
            name="ProbeSecret",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("secret", core.crypto.EncryptedTextField(null=True)),
                (
                    "chama",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="organizations.chama",
                    ),
                ),
            ],
        ),
        chama_rls("rls_probe_probesecret"),
    ]
