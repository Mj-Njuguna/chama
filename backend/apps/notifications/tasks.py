"""Notifications background jobs. Provider calls never run inside a database transaction."""

import uuid

from celery import shared_task

from apps.accounts.services import push_targets_for_user
from apps.notifications.messaging import apply_provider_status, deliver
from apps.notifications.push import send_push
from apps.notifications.whatsapp import statuses
from apps.organizations.services import member_contacts
from core.tenancy import chama_context


@shared_task(name="apps.notifications.tasks.deliver_message")  # type: ignore[untyped-decorator]
def deliver_message(chama_id: str, message_id: str, first_channel: str = "whatsapp") -> str:
    return deliver(uuid.UUID(chama_id), uuid.UUID(message_id), first_channel)


@shared_task(name="apps.notifications.tasks.push_to_members")  # type: ignore[untyped-decorator]
def push_to_members(chama_id: str, member_ids: list[str], kind: str, data: dict[str, str]) -> int:
    with chama_context(chama_id):
        users = [c.user_id for c in member_contacts([uuid.UUID(m) for m in member_ids]).values()]
    targets = [(t.token_type, t.token) for user in users for t in push_targets_for_user(user)]
    return send_push(targets, kind, {k: uuid.UUID(v) for k, v in data.items()})


@shared_task(name="apps.notifications.tasks.process_whatsapp_statuses")  # type: ignore[untyped-decorator]
def process_whatsapp_statuses(payload: dict[str, object]) -> int:
    found = statuses(payload)
    for wamid, status, code in found:
        apply_provider_status(wamid, status, code)
    return len(found)
