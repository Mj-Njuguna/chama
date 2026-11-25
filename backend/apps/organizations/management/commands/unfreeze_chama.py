"""Unfreeze a chama after manual review. Operators only; never self-service.

Usage: ``just manage unfreeze_chama <chama id> --operator jdoe``. Writes an
``chama_unfrozen`` security event with the operator, in the same transaction, and alerts the
officials.
"""

import re
import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.organizations.pending import unfreeze_chama

_OPERATOR = re.compile(r"^[a-z_]{1,40}$")


class Command(BaseCommand):
    help = "Clear a chama's freeze after manual review."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("chama_id")
        parser.add_argument(
            "--operator",
            required=True,
            help="Who reviewed it (lower-case letters and underscores), for the audit event.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        operator = options["operator"]
        if not _OPERATOR.match(operator):
            raise CommandError("--operator must be lower-case letters and underscores")
        try:
            chama_id = uuid.UUID(options["chama_id"])
        except ValueError:
            raise CommandError("chama_id must be a UUID") from None
        if not unfreeze_chama(chama_id, operator=operator):
            raise CommandError("no such chama, or it is not frozen")
        self.stdout.write(f"unfroze {chama_id}")
