"""Re-encrypt every encrypted field under the current key (key rotation step).

Usage: ``just manage reencrypt_fields`` (or ``--check`` to only count values still under an
older key). Run after prepending a new key to ``FIELD_ENCRYPTION_KEYS`` and deploying; remove
the old key only when ``--check`` reports 0.

- Walks every model with an :class:`~core.crypto.EncryptedTextField`. Chama tables are
  processed per chama (every chama row, not only active ones, so no ciphertext is left on
  the old key), each batch in its own short ``chama_context`` with the rows locked
  (``SELECT ... FOR UPDATE``), so a concurrent edit is never overwritten with an older value.
- Models without ``chama_id`` are refused until a reviewed path exists for them.
- Prints counts only; never a value.
"""

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, models
from django.db.models.functions import Cast

from core.crypto import EncryptedTextField, decrypt, needs_reencryption
from core.tenancy import chama_context

BATCH = 500


def encrypted_models() -> list[tuple[type[models.Model], list[EncryptedTextField]]]:
    found = []
    for model in apps.get_models():
        fields = [f for f in model._meta.concrete_fields if isinstance(f, EncryptedTextField)]
        if fields:
            found.append((model, fields))
    return found


def _rotate_batch(
    model: type[models.Model], fields: list[EncryptedTextField], after: Any, check: bool
) -> tuple[int, Any]:
    raw = {f"_raw_{f.attname}": Cast(f.attname, models.TextField()) for f in fields}
    rows = (
        model._default_manager.order_by("pk")
        .filter(**({"pk__gt": after} if after is not None else {}))
        .annotate(**raw)
        .values("pk", *raw)
    )
    if not check:
        rows = rows.select_for_update(of=("self",))
    batch = list(rows[:BATCH])
    stale = 0
    for row in batch:
        changes = {
            f.attname: decrypt(row[f"_raw_{f.attname}"], f.label)
            for f in fields
            if row[f"_raw_{f.attname}"] is not None and needs_reencryption(row[f"_raw_{f.attname}"])
        }
        if changes:
            stale += 1
            if not check:
                model._default_manager.filter(pk=row["pk"]).update(**changes)
    return stale, (batch[-1]["pk"] if len(batch) == BATCH else None)


class Command(BaseCommand):
    help = "Re-encrypt encrypted fields under the current FIELD_ENCRYPTION_KEYS key."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check", action="store_true", help="Only count values under an older key."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        check = options["check"]
        targets = encrypted_models()
        for model, _ in targets:
            names = {f.name for f in model._meta.concrete_fields}
            if "chama" not in names:
                raise CommandError(f"{model._meta.label}: no reviewed re-encryption path")
        with connection.cursor() as cursor:  # the tenant root is outside chama RLS
            cursor.execute("SELECT id FROM organizations_chama ORDER BY id")
            chama_ids = [row[0] for row in cursor.fetchall()]
        total = 0
        for model, fields in targets:
            count = 0
            for chama_id in chama_ids:
                after: Any = None
                while True:
                    with chama_context(chama_id):
                        stale, after = _rotate_batch(model, fields, after, check)
                    count += stale
                    if after is None:
                        break
            total += count
            verb = "on an older key" if check else "re-encrypted"
            self.stdout.write(f"{model._meta.label}: {count} rows {verb}")
        self.stdout.write(f"total: {total}")
