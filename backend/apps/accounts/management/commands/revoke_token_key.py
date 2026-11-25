"""Revoke the sessions derived with a compromised AUTH_TOKEN_KEYS key.

Usage: ``just manage revoke_token_key k1 --operator jdoe``. Run it after removing the key from
the ring, as part of a compromise rotation. A routine (scheduled) rotation does not need it.

The revocation and a ``token_key_revoked`` security event (operator, key ID, number of sessions
revoked) are written in one transaction: no revocation without its audit record.
"""

import re
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.sessions import revoke_sessions_derived_with

# The audited key ID shape (apps.accounts.audit): letters, then at most four digits.
_KEY_ID = re.compile(r"^[a-z]{1,8}[0-9]{0,4}$")
_OPERATOR = re.compile(r"^[a-z_]{1,40}$")


class Command(BaseCommand):
    help = "Revoke every live session whose current tokens were derived with the given key ID."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("key_id")
        parser.add_argument(
            "--operator",
            required=True,
            help="Who is running this (lower-case letters and underscores), for the audit event.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        key_id, operator = options["key_id"], options["operator"]
        if not _KEY_ID.match(key_id):
            raise CommandError("not a key ID (letters, then at most four digits, e.g. k1)")
        if not _OPERATOR.match(operator):
            raise CommandError("--operator must be lower-case letters and underscores")
        revoked = revoke_sessions_derived_with(key_id, operator=operator)
        self.stdout.write(f"revoked {revoked} session(s) derived with {key_id}")
