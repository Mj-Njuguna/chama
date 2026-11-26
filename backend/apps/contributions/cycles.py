"""Cycle and obligation generation from the constitution (internal).

Business dates are Africa/Nairobi. A cycle is one period (an ISO week starting Monday, or a
calendar month); its due date is the constitution's ``due_day`` in that period (weekday 0-6, or
day of month 1-28) and its grace ends ``grace_days`` later. A cycle is created once its due date
is at most ``CYCLE_LEAD_DAYS`` away (and its grace has not already ended), so pre-due
reminders always have a cycle to remind about, and every active member gets one contribution
obligation in it. Generation is idempotent (unique period per chama, unique obligation per cycle
and member).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from apps.contributions.models import Cycle, Obligation, ObligationKind
from apps.organizations.services import active_member_ids, rules_for_chama

CYCLE_LEAD_DAYS = 7


def today() -> date:
    """Today in Africa/Nairobi."""
    return datetime.now(ZoneInfo(settings.BUSINESS_TIME_ZONE)).date()


@dataclass(frozen=True)
class Period:
    start: date
    due_date: date


def _month_start(day: date, months_ahead: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + months_ahead
    return date(index // 12, index % 12 + 1, 1)


def periods_around(day: date, frequency: str, due_day: int) -> list[Period]:
    """The period containing ``day`` and the next one."""
    if frequency == "weekly":
        start = day - timedelta(days=day.weekday())
        starts = [start, start + timedelta(days=7)]
        return [Period(s, s + timedelta(days=due_day)) for s in starts]
    starts = [_month_start(day), _month_start(day, 1)]
    return [Period(s, s.replace(day=due_day)) for s in starts]


@dataclass(frozen=True)
class Generated:
    cycles: int
    obligations: int


def ensure_cycles(chama_id: uuid.UUID, on: date | None = None) -> Generated:
    """Create the cycles due within ``CYCLE_LEAD_DAYS`` of ``on`` (Nairobi today by default)
    and the missing obligations of every open cycle. Runs in the chama's context."""
    on = on or today()
    rules = rules_for_chama(chama_id)
    amount = int(rules["contribution_amount_minor"])
    cycles = obligations = 0
    with transaction.atomic():
        if amount > 0:
            for period in periods_around(on, rules["contribution_frequency"], rules["due_day"]):
                if period.due_date - timedelta(days=CYCLE_LEAD_DAYS) > on:
                    continue  # not yet
                if period.due_date + timedelta(days=rules["grace_days"]) < on:
                    continue  # already over (a chama that starts mid-period is not fined)
                cycle, created = Cycle.objects.get_or_create(
                    chama_id=chama_id,
                    period_start=period.start,
                    defaults={
                        "frequency": rules["contribution_frequency"],
                        "due_date": period.due_date,
                        "grace_until": period.due_date + timedelta(days=rules["grace_days"]),
                        "amount_minor": amount,
                    },
                )
                if created:
                    from apps.notifications.services import publish_feed_event

                    publish_feed_event(
                        chama_id,
                        "cycle_opened",
                        {
                            "cycle_id": str(cycle.id),
                            "due_date": cycle.due_date.isoformat(),
                            "amount_minor": cycle.amount_minor,
                        },
                        key=f"cycle:{cycle.id}:opened",
                    )
                cycles += created
        members = active_member_ids()
        # Members who joined during a cycle owe it from its due date on, like everyone else.
        for cycle in Cycle.objects.filter(status="open", grace_until__gte=on):
            have = set(
                Obligation.objects.filter(
                    cycle_id=cycle.id, kind=ObligationKind.CONTRIBUTION
                ).values_list("member_id", flat=True)
            )
            new = [
                Obligation(
                    chama_id=chama_id,
                    member_id=member,
                    kind=ObligationKind.CONTRIBUTION,
                    cycle_id=cycle.id,
                    due_date=cycle.due_date,
                    grace_until=cycle.grace_until,
                    amount_minor=cycle.amount_minor,
                )
                for member in members
                if member not in have
            ]
            Obligation.objects.bulk_create(new, ignore_conflicts=True)
            obligations += len(new)
    return Generated(cycles, obligations)
