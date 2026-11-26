"""The chama's in-app activity feed and member push (internal).

Feed events are visible to the chama's members only (RLS) and may carry amounts and member ids;
the app resolves names. Push payloads never carry amounts or names: a ``kind`` and IDs only.
"""

import uuid
from typing import Any

from django.db import IntegrityError, transaction

from apps.notifications.models import FeedEvent


def publish_feed_event(
    chama_id: uuid.UUID, kind: str, payload: dict[str, Any], key: str | None = None
) -> bool:
    """Add one feed entry in the caller's transaction. Idempotent on ``key`` (default: a new
    one). Returns False when that entry already exists."""
    try:
        with transaction.atomic():
            FeedEvent.objects.create(
                chama_id=chama_id, kind=kind, payload=payload, key=key or f"feed:{uuid.uuid4()}"
            )
    except IntegrityError:
        return False
    return True


def notify_push(
    chama_id: uuid.UUID, member_ids: list[uuid.UUID], kind: str, data: dict[str, uuid.UUID]
) -> None:
    """Push ``kind`` with ID-only ``data`` to the members' live devices, after commit."""
    from apps.notifications.tasks import push_to_members

    members = [str(m) for m in member_ids]
    values = {k: str(v) for k, v in data.items()}
    transaction.on_commit(lambda: push_to_members.delay(str(chama_id), members, kind, values))
