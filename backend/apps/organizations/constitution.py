"""The chama's constitution rules. Internal to organizations.

Every member may read them. Officials change them with a fresh server PIN (the policy function
runs again inside the write transaction, chama row locked). A change applies the given fields
only, checks the whole result, bumps ``version`` and writes a security event.
"""

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.services import SecurityEventKind, SessionIdentity, record_security_event
from apps.organizations.errors import chama_not_available
from apps.organizations.models import (
    Constitution,
    ContributionFrequency,
    InterestMethod,
    PaymentChannel,
    PenaltyType,
)
from apps.organizations.permissions import Action
from apps.organizations.stepup import chama_state, decide
from core.tenancy import current_chama_id

# KES 10,000,000 per contribution or flat fine, in minor units.
AMOUNT_MAX_MINOR = 1_000_000_000
EDITABLE_FIELDS = (
    "contribution_frequency",
    "contribution_amount_minor",
    "due_day",
    "grace_days",
    "penalty_type",
    "penalty_flat_minor",
    "penalty_rate_bp",
    "loan_multiplier",
    "interest_method",
    "interest_rate_bp",
    "max_loan_months",
    "payment_channel",
)


@dataclass(frozen=True)
class Rules:
    contribution_frequency: str
    contribution_amount_minor: int
    due_day: int
    grace_days: int
    penalty_type: str
    penalty_flat_minor: int
    penalty_rate_bp: int
    loan_multiplier: Decimal
    interest_method: str
    interest_rate_bp: int
    max_loan_months: int
    payment_channel: str
    version: int
    updated_at: datetime
    updated_by_user_id: uuid.UUID | None


def _rules(row: Constitution) -> Rules:
    return Rules(
        **{name: getattr(row, name) for name in EDITABLE_FIELDS},
        version=row.version,
        updated_at=row.updated_at,
        updated_by_user_id=row.updated_by_user_id,
    )


def _chama(identity: SessionIdentity) -> uuid.UUID:
    chama_id = current_chama_id()
    if chama_id is None or chama_id != identity.active_chama_id:
        raise chama_not_available()
    return chama_id


def get_rules(identity: SessionIdentity) -> Rules:
    chama_id = _chama(identity)
    return _rules(Constitution.objects.get(chama_id=chama_id))


def rules_for_chama(chama_id: uuid.UUID) -> dict[str, Any]:
    """The current rules as plain values, for other modules (inside the chama context)."""
    return asdict(_rules(Constitution.objects.get(chama_id=chama_id)))


def validate(values: dict[str, Any]) -> None:
    """Cross-field checks on a complete set of rules. Raises ValidationError per field."""
    errors: dict[str, list[str]] = {}
    frequency = values["contribution_frequency"]
    if frequency == ContributionFrequency.MONTHLY and not 1 <= values["due_day"] <= 28:
        errors["due_day"] = ["monthly_day_1_to_28"]
    if frequency == ContributionFrequency.WEEKLY and not 0 <= values["due_day"] <= 6:
        errors["due_day"] = ["weekday_0_to_6"]
    if not 0 <= values["contribution_amount_minor"] <= AMOUNT_MAX_MINOR:
        errors["contribution_amount_minor"] = ["out_of_range"]
    if not 0 <= values["grace_days"] <= 30:
        errors["grace_days"] = ["out_of_range"]
    penalty = values["penalty_type"]
    if penalty == PenaltyType.FLAT and not 0 < values["penalty_flat_minor"] <= AMOUNT_MAX_MINOR:
        errors["penalty_flat_minor"] = ["required_for_flat"]
    if penalty == PenaltyType.PERCENT and not 0 < values["penalty_rate_bp"] <= 10000:
        errors["penalty_rate_bp"] = ["required_for_percent"]
    if not 0 <= values["interest_rate_bp"] <= 10000:
        errors["interest_rate_bp"] = ["out_of_range"]
    multiplier = Decimal(values["loan_multiplier"])
    if not Decimal("0") <= multiplier <= Decimal("20") or multiplier != multiplier.quantize(
        Decimal("0.1")
    ):
        errors["loan_multiplier"] = ["out_of_range"]
    if not 1 <= values["max_loan_months"] <= 60:
        errors["max_loan_months"] = ["out_of_range"]
    for name, choices in (
        ("contribution_frequency", ContributionFrequency.values),
        ("penalty_type", PenaltyType.values),
        ("interest_method", InterestMethod.values),
        ("payment_channel", PaymentChannel.values),
    ):
        if values[name] not in choices:
            errors[name] = ["invalid"]
    if errors:
        raise ValidationError(errors)


def update_rules(identity: SessionIdentity, caller_role: object, changes: dict[str, Any]) -> Rules:
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationError({name: ["not_editable"] for name in sorted(unknown)})
    chama_id = _chama(identity)
    with transaction.atomic():
        decision = decide(
            caller_role,
            Action.EDIT_CONSTITUTION,
            identity,
            timezone.now(),
            chama_state(chama_id, identity, lock=True),
        )
        if decision.error is not None:
            raise decision.error
        row = Constitution.objects.select_for_update().get(chama_id=chama_id)
        values = {name: getattr(row, name) for name in EDITABLE_FIELDS} | changes
        validate(values)
        if all(getattr(row, name) == value for name, value in changes.items()):
            return _rules(row)  # nothing changed: no new version
        for name, value in changes.items():
            setattr(row, name, value)
        row.version += 1
        row.updated_by_user_id = identity.user_id
        row.save()
        record_security_event(
            SecurityEventKind.CONSTITUTION_CHANGED,
            chama_id=chama_id,
            user_id=identity.user_id,
            device_id=identity.device_id,
            session_id=identity.session_id,
            metadata={"version": min(row.version, 9999)},
        )
        return _rules(row)
