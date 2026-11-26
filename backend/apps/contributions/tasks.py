"""Contribution jobs (Celery Beat). Times are in the beat schedule (settings).

``dispatch(job)`` fans out one task per active chama, spread over
``CONTRIBUTIONS_JOB_JITTER_SECONDS``. Each per-chama job is idempotent: cycles and obligations
are unique per period and member, fines unique per late contribution (claimed with
``FOR UPDATE SKIP LOCKED``), messages unique per idempotency key, STK prompts keyed per
obligation. Provider calls (messages, STK) never run inside the chama transaction: messages are
queued and sent after commit; STK prompts are requested one by one outside the job's
transaction.
"""

import random
import uuid
from datetime import date, timedelta
from importlib import import_module

from celery import shared_task
from django.conf import settings

from apps.contributions.cycles import ensure_cycles, today
from apps.contributions.fines import assess_fines
from apps.contributions.models import Obligation
from apps.contributions.summaries import obligations_due_on
from apps.notifications.services import format_kes, publish_feed_event, send_member_message
from apps.organizations.services import chama_name, member_contacts, member_names
from core.api_errors import ApiError
from core.logging import get_logger
from core.tenancy import active_chama_ids, chama_context

log = get_logger(__name__)

PRE_REMINDER_DAYS = 3
JOBS = ("generate_cycles", "assess_fines", "pre_reminders", "due_reminders")


@shared_task(name="apps.contributions.tasks.dispatch")  # type: ignore[untyped-decorator]
def dispatch(job: str) -> int:
    if job not in JOBS:
        raise ValueError(job)
    task = {
        "generate_cycles": generate_cycles,
        "assess_fines": fine_late_contributions,
        "pre_reminders": pre_reminders,
        "due_reminders": due_reminders,
    }[job]
    chamas = active_chama_ids()
    spread = settings.CONTRIBUTIONS_JOB_JITTER_SECONDS
    for chama_id in chamas:
        countdown = random.randint(0, spread) if spread else 0  # noqa: S311  not security
        task.apply_async(kwargs={"chama_id": str(chama_id)}, countdown=countdown)
    return len(chamas)


@shared_task(name="apps.contributions.tasks.generate_cycles")  # type: ignore[untyped-decorator]
def generate_cycles(chama_id: str, on: str | None = None) -> int:
    with chama_context(chama_id):
        made = ensure_cycles(uuid.UUID(chama_id), _date(on))
    return made.obligations


@shared_task(name="apps.contributions.tasks.fine_late_contributions")  # type: ignore[untyped-decorator]
def fine_late_contributions(chama_id: str, on: str | None = None) -> int:
    chama = uuid.UUID(chama_id)
    with chama_context(chama):
        issued = assess_fines(chama, _date(on))
        name = chama_name(chama)
        names = member_names([f.member_id for f in issued])
        for fine in issued:
            publish_feed_event(
                chama,
                "fine_assessed",
                {
                    "member_id": str(fine.member_id),
                    "amount_minor": fine.amount_minor,
                    "member_name": names.get(fine.member_id) or None,
                },
                key=f"fine:{fine.fine_id}",
            )
            send_member_message(
                chama,
                fine.member_id,
                "fine_notice",
                [name, format_kes(fine.amount_minor)],
                f"fine:{fine.fine_id}:notice",
            )
    return len(issued)


def _remind(chama: uuid.UUID, due_on: date, kind: str) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Queue reminders for contributions due on ``due_on``; returns (member, obligation, balance)
    for each one that still has a balance."""
    with chama_context(chama):
        due = obligations_due_on(due_on)
        name = chama_name(chama)
        for item in due:
            send_member_message(
                chama,
                item.member_id,
                "contribution_reminder",
                [name, format_kes(item.balance_minor), due_on.isoformat()],
                f"obligation:{item.obligation_id}:reminder:{kind}",
            )
        return [(d.member_id, d.obligation_id, d.balance_minor) for d in due]


@shared_task(name="apps.contributions.tasks.pre_reminders")  # type: ignore[untyped-decorator]
def pre_reminders(chama_id: str, on: str | None = None) -> int:
    day = _date(on) or today()
    return len(_remind(uuid.UUID(chama_id), day + timedelta(days=PRE_REMINDER_DAYS), "pre"))


@shared_task(name="apps.contributions.tasks.due_reminders")  # type: ignore[untyped-decorator]
def due_reminders(chama_id: str, on: str | None = None) -> int:
    """Due-date reminders, then the M-Pesa prompt for members who opted in."""
    chama = uuid.UUID(chama_id)
    due = _remind(chama, _date(on) or today(), "due")
    return prompt_opted_in(chama, due)


def prompt_opted_in(chama: uuid.UUID, due: list[tuple[uuid.UUID, uuid.UUID, int]]) -> int:
    """Request one STK prompt per opted-in member with a balance (idempotent per obligation).
    Skipped cleanly while the chama has no active payment linking (``payments_not_linked``)."""
    # Looked up at run time: the payments module provides it; tolerate its absence.
    request_stk_push = getattr(import_module("apps.payments.services"), "request_stk_push", None)
    if request_stk_push is None:
        return 0
    with chama_context(chama):
        contacts = member_contacts([member for member, _, _ in due])
    prompted = 0
    for member, obligation, balance in due:
        contact = contacts.get(member)
        if contact is None or not contact.consented or not contact.scheduled_stk_opt_in:
            continue
        try:
            # One short chama transaction per member, like any services call (the payments
            # module sends the prompt itself after commit).
            with chama_context(chama):
                if not Obligation.objects.filter(id=obligation, status="open").exists():
                    continue
                request_stk_push(
                    chama,
                    member_id=member,
                    amount_minor=balance,
                    reason="scheduled",
                    idempotency_key=f"obligation:{obligation}:stk",
                )
            prompted += 1
        except ApiError as error:  # payments_not_linked and other refusals: a clean skip
            log.info("scheduled_stk_skipped", reason=error.extra.get("reason", error.error_code))
        except Exception:  # deliberately broad: one member never stops the others
            log.warning("scheduled_stk_failed")
    return prompted


def _date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None
