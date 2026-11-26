"""The step-up policy. Internal to organizations; used by RequiresAction.

ONE function decides every sensitive action:
``decide(role, action, session) -> allow | step_up_required(factors) | pin_not_set | deny``.

Factors per action:

=====================  ============  ==========================================
action                 factors       notes
=====================  ============  ==========================================
view_*                 none          an unlocked session (local unlock) only
record_receipt         pin           PIN fresh for 15 min
allocate_payment       pin
waive_fine             pin           requester and approver must differ
edit_constitution      pin
manage_members         otp           OTP fresh for 5 min
change_roles           otp
export_data            otp, pin
payment_linking        otp           + cooling-off, alerts, freeze
=====================  ============  ==========================================

Freshness is per session (kept on the session row by the accounts module). A missing session
(no verified token) never counts as fresh.

Chama state: a frozen chama ("this wasn't me") refuses every step-up action with
``chama_frozen``. The highest-risk actions (``HIGH_RISK``) also answer ``cooling_off``
(``retry_after``) while:

- the user has an open forgot-PIN reset (sensitive actions wait until it completes);
- (a) the chama has an open cooling-off change requested before this device was first seen;
- (b) this device was first seen less than ``NEW_DEVICE_WINDOW_HOURS`` ago, unless the chama
  itself is younger than that window.

``RequiresAction`` passes the state read at request time; an action that changes data calls
``decide`` again inside its own transaction with the chama row locked
(``chama_state(..., lock=True)``), so the answer cannot go stale before the write.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.services import (
    SessionIdentity,
    device_first_seen,
    has_pin,
    pin_not_set,
    reset_effective_at,
    step_up_required,
)
from apps.organizations.models import Chama, PendingChange, PendingChangeKind, PendingChangeStatus
from apps.organizations.permissions import Action, is_allowed
from core.api_errors import ApiError, chama_frozen, cooling_off

STEP_UP: dict[Action, tuple[str, ...]] = {
    Action.RECORD_RECEIPT: ("pin",),
    Action.ALLOCATE_PAYMENT: ("pin",),
    Action.WAIVE_FINE: ("pin",),
    Action.EDIT_CONSTITUTION: ("pin",),
    Action.MANAGE_MEMBERS: ("otp",),
    Action.CHANGE_ROLES: ("otp",),
    Action.EXPORT_DATA: ("otp", "pin"),
    Action.PAYMENT_LINKING: ("otp",),
}

# The actions a new device, or a user with an open PIN reset, must wait for.
HIGH_RISK: frozenset[Action] = frozenset(
    {
        Action.PAYMENT_LINKING,
        Action.CHANGE_ROLES,
        Action.EXPORT_DATA,
    }
)

# The action (and so the step-up) that requests and applies each cooling-off change.
CHANGE_ACTIONS: dict[str, Action] = {
    PendingChangeKind.PAYMENT_DETAILS: Action.PAYMENT_LINKING,
}


@dataclass(frozen=True)
class ChamaState:
    frozen: bool
    created_at: datetime | None = None
    device_first_seen: datetime | None = None
    # Effective times of open cooling-off changes requested before this device was first seen.
    changes_before_device: tuple[datetime, ...] = ()
    pin_reset_effective_at: datetime | None = None


def chama_state(
    chama_id: uuid.UUID | None, identity: SessionIdentity | None = None, *, lock: bool = False
) -> ChamaState | None:
    """The facts the policy needs about the chama, this device and the user's PIN reset.
    ``lock=True`` inside the action's transaction (locks the chama row)."""
    if chama_id is None:
        return None
    rows = Chama.objects.filter(id=chama_id)
    if lock:
        rows = rows.select_for_update()
    found = rows.values_list("frozen_at", "created_at").first()
    if found is None:
        return None
    frozen_at, created_at = found
    if identity is None:
        return ChamaState(frozen=frozen_at is not None, created_at=created_at)
    now = timezone.now()
    first_seen = device_first_seen(identity.user_id, identity.device_id)
    before: tuple[datetime, ...] = ()
    if first_seen is not None:
        before = tuple(
            PendingChange.objects.filter(
                chama_id=chama_id,
                status=PendingChangeStatus.PENDING,
                expires_at__gt=now,
                requested_at__lt=first_seen,
            ).values_list("effective_at", flat=True)
        )
    return ChamaState(
        frozen=frozen_at is not None,
        created_at=created_at,
        device_first_seen=first_seen,
        changes_before_device=before,
        pin_reset_effective_at=reset_effective_at(identity.user_id, now),
    )


def _wait(until: datetime, now: datetime) -> int:
    return max(1, int((until - now).total_seconds()))


def _new_device_wait(chama: ChamaState, now: datetime) -> ApiError | None:
    """``cooling_off`` for a high-risk action, or None (see the module docstring)."""
    if chama.pin_reset_effective_at is not None:
        return cooling_off(_wait(chama.pin_reset_effective_at, now))
    if chama.changes_before_device:  # (a)
        return cooling_off(_wait(max(chama.changes_before_device), now))
    window = timedelta(hours=settings.NEW_DEVICE_WINDOW_HOURS)
    first_seen, created = chama.device_first_seen, chama.created_at
    young_chama = created is not None and now - created < window
    if first_seen is not None and now - first_seen < window and not young_chama:  # (b)
        return cooling_off(_wait(first_seen + window, now))
    return None


@dataclass(frozen=True)
class Decision:
    error: ApiError | PermissionDenied | None = None

    @property
    def allowed(self) -> bool:
        return self.error is None


def fresh(identity: SessionIdentity | None, factor: str, now: datetime) -> bool:
    """Whether this session verified ``factor`` recently enough."""
    if identity is None:
        return False
    at, window = {
        "pin": (identity.step_up_pin_at, settings.STEP_UP_PIN_FRESH_SECONDS),
        "otp": (identity.step_up_otp_at, settings.STEP_UP_OTP_FRESH_SECONDS),
    }[factor]
    return at is not None and now - at <= timedelta(seconds=window)


def role_allows(role: object, action: object) -> bool:
    """The role check alone, for actions WITHOUT step-up (exported as ``services.is_allowed``).

    A step-up action is reachable only through ``RequiresAction`` (which also runs ``decide``),
    so asking here for one is a programming error, never a silent bypass.
    """
    if action in STEP_UP:
        raise ValueError(f"{action} needs step-up: guard it with RequiresAction")
    return is_allowed(role, action)


def decide(
    role: object,
    action: Action,
    identity: SessionIdentity | None,
    now: datetime,
    chama: ChamaState | None = None,
) -> Decision:
    """Order: role, the chama's freeze, new-device waits, ``pin_not_set``, stale factors."""
    if not is_allowed(role, action):
        return Decision(PermissionDenied("Your role does not allow this."))
    needed = STEP_UP.get(action, ())
    if not needed:
        return Decision()
    if chama is not None and chama.frozen:
        return Decision(chama_frozen())
    if chama is not None and action in HIGH_RISK and (wait := _new_device_wait(chama, now)):
        return Decision(wait)
    if "pin" in needed and (identity is None or not has_pin(identity.user_id)):
        return Decision(pin_not_set())
    missing = [factor for factor in needed if not fresh(identity, factor, now)]
    if missing:
        return Decision(step_up_required(missing))
    return Decision()
