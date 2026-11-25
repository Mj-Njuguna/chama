import uuid

from django.db import models

from core.crypto import EncryptedTextField


class ProbeRecord(models.Model):
    """Stands in for any chama-owned table (contributions, loans, ledger entries)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT)
    name = models.CharField(max_length=64)

    def __str__(self) -> str:
        return self.name


class ProbeSecret(models.Model):
    """Stands in for any chama table with an encrypted field (payment credentials, KYC)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("organizations.Chama", on_delete=models.PROTECT, related_name="+")
    secret = EncryptedTextField(null=True)

    def __str__(self) -> str:
        return str(self.id)
