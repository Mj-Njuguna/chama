"""Cooling-off chama changes and the "this wasn't me" freeze.

Internal to organizations. THE ONLY MODULE that writes ``organizations_pendingchange`` or
``organizations_chama.frozen_at``.

- **Request** (payment linking): the action's step-up (fresh OTP) through the
  policy function, then a pending row that becomes applicable after
  ``CHAMA_CHANGE_COOLING_OFF_HOURS`` and expires ``PENDING_CHANGE_EXPIRY_HOURS`` later. Every
  official is alerted. One open change per kind per chama.
- **Apply**: an official with the same step-up, inside the window. The chama row is locked and
  the policy function runs again in the same transaction, so a freeze pressed meanwhile wins.
  The module that owns the details (payments) applies them in the same outer transaction.
- **Cancel**: any official, no step-up. **Freeze**: any official, no step-up; cancels every open
  change, alerts every official, and blocks every step-up action until an operator clears it
  after manual review (``unfreeze_chama``).
- Lock order is always the chama row, then change rows. ``chama_id`` is always the
  verified session's active chama (never request input).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import (
    MAX_METADATA_INT,
    SecurityEventKind,
    SessionIdentity,
    record_security_event,
)
from apps.organizations.errors import chama_not_available
from apps.organizations.models import (
    Chama,
    PendingChange,
    PendingChangeKind,
    PendingChangeStatus,
)
from apps.organizations.roles import OFFICIALS
from apps.organizations.selectors import memberships_for_user
from apps.organizations.stepup import CHANGE_ACTIONS, chama_state, decide
from core.api_errors import ApiError, cooling_off
from core.tenancy import chama_context, current_chama_id


def change_not_found() -> ApiError:
    """Unknown, another chama's, or no longer pending (applied, cancelled, expired)."""
    return ApiError("not_found", "No such pending change.", status_code=404)


@dataclass(frozen=True)
class ChangeSummary:
    id: uuid.UUID
    kind: str
    status: str
    requested_by_user_id: uuid.UUID
    requested_at: datetime
    effective_at: datetime
    expires_at: datetime


def _summary(row: PendingChange) -> ChangeSummary:
    return ChangeSummary(
        row.id,
        row.kind,
        row.status,
        row.requested_by_user_id,
        row.requested_at,
        row.effective_at,
        row.expires_at,
    )


def _chama_id(identity: SessionIdentity) -> uuid.UUID:
    chama_id = current_chama_id()
    if chama_id is None or chama_id != identity.active_chama_id:
        raise chama_not_available()
    return chama_id


def _seconds(until: datetime, now: datetime) -> int:
    return max(1, int((until - now).total_seconds()))


def _parse(change_id: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(change_id))
    except ValueError:
        raise change_not_found() from None


def _alert_officials(chama_id: uuid.UUID, kind: str, change_id: uuid.UUID | None = None) -> None:
    """After commit, push ``kind`` to every official's devices (a task: each official's push
    tokens are only readable in that official's own user context, never inside this request's)."""
    from apps.organizations.tasks import alert_chama_officials

    data = {"change_id": str(change_id)} if change_id else {}
    transaction.on_commit(lambda: alert_chama_officials.delay(str(chama_id), kind, data))


def _event(
    kind: SecurityEventKind, identity: SessionIdentity, chama_id: uuid.UUID, **meta: object
) -> None:
    record_security_event(
        kind,
        chama_id=chama_id,
        user_id=identity.user_id,
        device_id=identity.device_id,
        session_id=identity.session_id,
        metadata=meta or None,
    )


def request_change(identity: SessionIdentity, role: object, kind: str) -> ChangeSummary:
    kind = PendingChangeKind(kind)
    chama_id = _chama_id(identity)
    now = timezone.now()
    with transaction.atomic():
        state = chama_state(chama_id, identity, lock=True)
        decision = decide(role, CHANGE_ACTIONS[kind], identity, now, state)
        if decision.error is not None:
            raise decision.error
        open_change = (
            PendingChange.objects.select_for_update()
            .filter(chama_id=chama_id, kind=kind, status=PendingChangeStatus.PENDING)
            .first()
        )
        if open_change is not None and now < open_change.expires_at:
            raise cooling_off(_seconds(open_change.effective_at, now))  # apply or cancel it first
        if open_change is not None:
            open_change.status, open_change.closed_at = PendingChangeStatus.EXPIRED, now
            open_change.save(update_fields=["status", "closed_at"])
        effective = now + timedelta(hours=settings.CHAMA_CHANGE_COOLING_OFF_HOURS)
        row = PendingChange.objects.create(
            chama_id=chama_id,
            kind=kind,
            requested_by_user_id=identity.user_id,
            requested_device_id=identity.device_id,
            requested_at=now,
            effective_at=effective,
            expires_at=effective + timedelta(hours=settings.PENDING_CHANGE_EXPIRY_HOURS),
        )
        _event(SecurityEventKind.CHANGE_REQUESTED, identity, chama_id, reason=kind.value)
        _alert_officials(chama_id, "chama_change_requested", row.id)
    return _summary(row)


def apply_change(identity: SessionIdentity, role: object, change_id: object) -> ChangeSummary:
    parsed = _parse(change_id)
    chama_id = _chama_id(identity)
    now = timezone.now()
    error: ApiError | None = None
    with transaction.atomic():
        state = chama_state(chama_id, identity, lock=True)  # the chama first, then the row
        row = PendingChange.objects.select_for_update().filter(id=parsed).first()
        if row is None or row.status != PendingChangeStatus.PENDING:
            raise change_not_found()
        decision = decide(role, CHANGE_ACTIONS[row.kind], identity, now, state)
        if decision.error is not None:
            raise decision.error  # a freeze pressed during the cooling-off wins here
        if now >= row.expires_at:
            row.status, row.closed_at = PendingChangeStatus.EXPIRED, now
            row.save(update_fields=["status", "closed_at"])
            error = change_not_found()
        elif now < row.effective_at:
            raise cooling_off(_seconds(row.effective_at, now))
        else:
            # The owning module (payments) applies the details in the caller's transaction.
            row.status, row.closed_at = PendingChangeStatus.APPLIED, now
            row.closed_by_user_id = identity.user_id
            row.save(update_fields=["status", "closed_at", "closed_by_user_id"])
            _event(SecurityEventKind.CHANGE_APPLIED, identity, chama_id, reason=row.kind)
            _alert_officials(chama_id, "chama_change_applied", row.id)
    if error is not None:
        raise error  # the expiry is already committed
    return _summary(row)


def cancel_change(identity: SessionIdentity, change_id: object) -> ChangeSummary:
    """Any official (RequiresAction REVIEW_CHAMA_CHANGES), no step-up, even while frozen."""
    parsed = _parse(change_id)
    chama_id = _chama_id(identity)
    now = timezone.now()
    with transaction.atomic():
        row = PendingChange.objects.select_for_update().filter(id=parsed).first()
        if row is None or row.status != PendingChangeStatus.PENDING or now >= row.expires_at:
            raise change_not_found()
        row.status, row.closed_at = PendingChangeStatus.CANCELLED, now
        row.closed_by_user_id = identity.user_id
        row.save(update_fields=["status", "closed_at", "closed_by_user_id"])
        _event(SecurityEventKind.CHANGE_CANCELLED, identity, chama_id, reason=row.kind)
        _alert_officials(chama_id, "chama_change_cancelled", row.id)
    return _summary(row)


def open_changes(identity: SessionIdentity) -> list[ChangeSummary]:
    _chama_id(identity)
    now = timezone.now()
    rows = PendingChange.objects.filter(
        status=PendingChangeStatus.PENDING, expires_at__gt=now
    ).order_by("requested_at")
    return [_summary(row) for row in rows]


def freeze_chama(identity: SessionIdentity) -> datetime:
    """ "This wasn't me": any official, no step-up. Idempotent; the first press alerts the
    officials."""
    chama_id = _chama_id(identity)
    now = timezone.now()
    with transaction.atomic():
        chama = Chama.objects.select_for_update().get(id=chama_id)
        if chama.frozen_at is not None:
            return chama.frozen_at
        chama.frozen_at, chama.frozen_by_user_id = now, identity.user_id
        chama.save(update_fields=["frozen_at", "frozen_by_user_id"])
        cancelled = PendingChange.objects.filter(status=PendingChangeStatus.PENDING).update(
            status=PendingChangeStatus.CANCELLED, closed_at=now, closed_by_user_id=identity.user_id
        )
        _event(
            SecurityEventKind.CHAMA_FROZEN,
            identity,
            chama_id,
            count=min(cancelled, MAX_METADATA_INT),
        )
        _alert_officials(chama_id, "chama_frozen")
    return now


def unfreeze_chama(chama_id: uuid.UUID, *, operator: str) -> bool:
    """Operator only, after manual review (management command). False if it was not frozen."""
    with chama_context(chama_id), transaction.atomic():
        chama = Chama.objects.select_for_update().filter(id=chama_id).first()
        if chama is None or chama.frozen_at is None:
            return False
        chama.frozen_at, chama.frozen_by_user_id = None, None
        chama.save(update_fields=["frozen_at", "frozen_by_user_id"])
        record_security_event(
            SecurityEventKind.CHAMA_UNFROZEN,
            chama_id=chama_id,
            metadata={"operator": operator},
        )
        _alert_officials(chama_id, "chama_unfrozen")
    return True


def official_of_frozen_chama(user_id: uuid.UUID) -> bool:
    """Whether a chama the user is an active official of is frozen; locks those chama rows.
    For forgot-PIN completion (no chama context: memberships across chamas)."""
    owned = [
        m.chama_id
        for m in memberships_for_user(user_id)
        if m.role in OFFICIALS and m.status == "active"
    ]
    rows = Chama.objects.select_for_update().filter(id__in=owned, frozen_at__isnull=False)
    return rows.exists()
