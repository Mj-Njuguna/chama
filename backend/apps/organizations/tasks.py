"""Organizations background jobs."""

import uuid

from celery import shared_task

from apps.accounts.services import push_targets_for_user
from apps.notifications.services import send_push
from apps.organizations.models import Membership, MembershipStatus
from apps.organizations.roles import OFFICIALS
from core.tenancy import chama_context


@shared_task(name="apps.organizations.tasks.alert_chama_officials")  # type: ignore[untyped-decorator]
def alert_chama_officials(chama_id: str, kind: str, data: dict[str, str]) -> int:
    """Push ``kind`` (with ID-only ``data``) to every active official's live devices.

    Queued after commit. The official list is read in a short chama context; each official's
    targets in that official's own user context; the send happens outside both (no network call
    inside a transaction).
    """
    with chama_context(chama_id):
        officials = list(
            Membership.objects.filter(
                chama_id=chama_id, role__in=list(OFFICIALS), status=MembershipStatus.ACTIVE
            ).values_list("user_id", flat=True)
        )
    targets = [(t.token_type, t.token) for user in officials for t in push_targets_for_user(user)]
    return send_push(targets, kind, {key: uuid.UUID(value) for key, value in data.items()})


@shared_task(name="apps.organizations.tasks.send_invitations")  # type: ignore[untyped-decorator]
def send_invitations(chama_id: str, invites: list[list[str]]) -> int:
    """Send one invitation message per ``(phone, member_code)``, after the invite committed.
    A failed message never undoes the invitation: the invitee still sees it in the app."""
    from apps.notifications.services import send_invite
    from apps.organizations.models import Chama

    with chama_context(chama_id):
        name = Chama.objects.filter(id=chama_id).values_list("name", flat=True).first() or ""
    return sum(1 for phone, code in invites if send_invite(phone, name, code).sent)
