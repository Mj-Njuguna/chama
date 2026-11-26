"""Payments background jobs."""

import uuid

from celery import shared_task
from django.utils import timezone

from apps.payments import fake
from apps.payments.confirm import apply_stk_result, process_c2b_confirmation
from apps.payments.gateway import GatewayError, gateway_configured, get_gateway
from apps.payments.linking import active_credentials, callback_url
from apps.payments.models import CallbackEvent, CallbackKind, StkRequest, StkStatus
from apps.payments.stk import sweep_stale_requests
from core.logging import get_logger
from core.tenancy import ChamaTask, active_chama_ids, chama_context

log = get_logger(__name__)

C2B_CONFIRMATION_PATH = "c2b/{token}/confirmation"
C2B_VALIDATION_PATH = "c2b/{token}/validation"


@shared_task(base=ChamaTask, name="apps.payments.tasks.process_callback")  # type: ignore[untyped-decorator]
def process_callback(*, chama_id: str, event_id: str) -> str:
    """Process one stored provider callback (runs in the chama's context). Idempotent: a
    processed event is skipped, and payments are unique per receipt anyway."""
    event = CallbackEvent.objects.select_for_update().filter(id=event_id).first()
    if event is None or event.processed_at is not None:
        return "skipped"
    if event.kind == CallbackKind.C2B_CONFIRMATION:
        outcome = process_c2b_confirmation(event)
    elif event.kind == CallbackKind.STK_RESULT:
        try:
            result = get_gateway().parse_stk_callback(event.body)
        except (ValueError, GatewayError):
            outcome = "invalid_body"
        else:
            outcome = apply_stk_result(result)
    else:
        outcome = "logged"
    event.processed_at, event.outcome = timezone.now(), outcome[:40]
    event.save(update_fields=["processed_at", "outcome"])
    return outcome


@shared_task(name="apps.payments.tasks.register_c2b_urls")  # type: ignore[untyped-decorator]
def register_c2b_urls(*, chama_id: str) -> bool:
    """Register the chama's confirmation and validation URLs with its shortcode. The provider
    call runs outside any transaction."""
    if not gateway_configured():
        return False
    with chama_context(chama_id):
        linked = active_credentials()
    if linked is None:
        return False
    credentials, token = linked
    try:
        get_gateway().register_c2b_urls(
            credentials,
            confirmation_url=callback_url(token, C2B_CONFIRMATION_PATH),
            validation_url=callback_url(token, C2B_VALIDATION_PATH),
        )
    except GatewayError:
        log.warning("c2b_register_failed", chama_id=chama_id)
        return False
    return True


@shared_task(name="apps.payments.tasks.sweep_stk_timeouts")  # type: ignore[untyped-decorator]
def sweep_stk_timeouts() -> int:
    """Beat job: time out stale STK requests in every chama."""
    return sum(sweep_stale_requests(uuid.UUID(str(chama))) for chama in active_chama_ids())


@shared_task(base=ChamaTask, name="apps.payments.tasks.fake_complete_stk")  # type: ignore[untyped-decorator]
def fake_complete_stk(*, chama_id: str, stk_request_id: str) -> str:
    """Development only (FAKE_MPESA_AUTOCOMPLETE_SECONDS): answer a pending fake STK request
    with a successful callback, through the same path a real callback takes."""
    row = StkRequest.objects.filter(id=stk_request_id, status=StkStatus.PENDING).first()
    if row is None or not row.checkout_request_id:
        return "skipped"
    event = CallbackEvent.objects.create(
        chama_id=chama_id,
        kind=CallbackKind.STK_RESULT,
        body=fake.stk_callback_body(row.checkout_request_id, amount_minor=row.amount_minor),
    )
    return str(process_callback.run(chama_id=chama_id, event_id=str(event.id)))
