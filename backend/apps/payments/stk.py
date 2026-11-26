"""STK (M-Pesa Express) payment prompts (internal).

- A request row is committed BEFORE the provider is called, so a crash never loses track of a
  prompt the member may answer. The provider call runs outside any database transaction.
- ``idempotency_key`` is unique per chama: asking twice returns the first request.
- A request still pending after ``STK_TIMEOUT_SECONDS`` is asked about once (STK Query through
  the gateway) and otherwise marked ``timeout``. A success that arrives later still records the
  payment.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.services import user_phone
from apps.organizations.services import member_user_id
from apps.payments.confirm import apply_stk_result
from apps.payments.gateway import GatewayError, gateway_configured, get_gateway
from apps.payments.linking import active_credentials, callback_url
from apps.payments.models import StkReason, StkRequest, StkStatus
from core.api_errors import ApiError, state_conflict
from core.logging import get_logger
from core.tenancy import chama_context

log = get_logger(__name__)

STK_PATH = "stk-result/{token}"


@dataclass(frozen=True)
class StkRequestView:
    stk_request_id: uuid.UUID
    status: str
    amount_minor: int
    mpesa_receipt: str | None
    created_at: datetime


def payments_unavailable() -> ApiError:
    return ApiError(
        "service_unavailable", "M-Pesa payments are not available right now.", status_code=503
    )


def _view(row: StkRequest) -> StkRequestView:
    receipt = None
    if row.payment_id is not None:
        from apps.payments.models import Payment

        receipt = (
            Payment.objects.filter(id=row.payment_id)
            .values_list("mpesa_receipt", flat=True)
            .first()
        )
    return StkRequestView(row.id, row.status, row.amount_minor, receipt, row.created_at)


def request_stk_push(
    chama_id: uuid.UUID,
    *,
    member_id: uuid.UUID,
    amount_minor: int,
    reason: str,
    idempotency_key: str,
) -> StkRequestView:
    """Ask M-Pesa to prompt the member's phone. Runs in the chama's context."""
    reason = StkReason(reason)
    if not gateway_configured():
        raise payments_unavailable()
    linked = active_credentials()
    if linked is None:
        raise state_conflict("payments_not_linked", "This chama has not linked its paybill.")
    if member_user_id(member_id) is None:
        raise state_conflict("member_not_active", "The member is not active in this chama.")
    try:
        with transaction.atomic():
            row = StkRequest.objects.create(
                chama_id=chama_id,
                member_id=member_id,
                amount_minor=amount_minor,
                reason=reason,
                idempotency_key=idempotency_key[:120],
            )
    except IntegrityError:
        existing = StkRequest.objects.get(idempotency_key=idempotency_key[:120])
        return _view(existing)
    if not transaction.get_autocommit():
        # Inside the caller's transaction (tests, scheduled jobs): call the provider after
        # commit so the row exists first.
        transaction.on_commit(lambda: _send(chama_id, row.id))
    else:
        _send(chama_id, row.id)
    row.refresh_from_db()
    return _view(row)


def _send(chama_id: uuid.UUID, request_id: uuid.UUID) -> None:
    with chama_context(chama_id):
        row = StkRequest.objects.filter(id=request_id, status=StkStatus.PENDING).first()
        linked = active_credentials()
        if row is None or linked is None:
            return
        credentials, token = linked
        user_id = member_user_id(row.member_id)
        phone = user_phone(user_id) if user_id else ""
        account_reference = _member_code(row.member_id)
    try:
        accepted = get_gateway().stk_push(
            credentials,
            phone_e164=phone,
            amount_minor=row.amount_minor,
            account_reference=account_reference,
            description="Chama contribution",
            callback_url=callback_url(token, STK_PATH),
        )
    except GatewayError:
        log.warning("stk_push_failed", stk_request_id=str(request_id))
        with chama_context(chama_id):
            StkRequest.objects.filter(id=request_id, status=StkStatus.PENDING).update(
                status=StkStatus.FAILED, result_code="gateway", completed_at=timezone.now()
            )
        return
    with chama_context(chama_id):
        StkRequest.objects.filter(id=request_id).update(
            checkout_request_id=accepted.checkout_request_id,
            merchant_request_id=accepted.merchant_request_id[:80],
        )
    delay = settings.FAKE_MPESA_AUTOCOMPLETE_SECONDS
    if delay:
        from apps.payments.tasks import fake_complete_stk

        fake_complete_stk.apply_async(
            kwargs={"chama_id": str(chama_id), "stk_request_id": str(request_id)},
            countdown=delay,
        )


def _member_code(member_id: uuid.UUID) -> str:
    from apps.organizations.services import member_directory

    for ref in member_directory():
        if ref.member_id == member_id:
            return ref.member_code
    return ""


def stk_status(stk_request_id: uuid.UUID) -> StkRequestView:
    row = StkRequest.objects.filter(id=stk_request_id).first()
    if row is None:
        raise LookupError(stk_request_id)
    return _view(row)


def own_stk_request(stk_request_id: uuid.UUID, member_id: uuid.UUID) -> StkRequestView | None:
    row = StkRequest.objects.filter(id=stk_request_id, member_id=member_id).first()
    return None if row is None else _view(row)


def sweep_stale_requests(chama_id: uuid.UUID) -> int:
    """Pending requests older than the timeout: ask the gateway once (outside any transaction),
    else mark ``timeout``."""
    cutoff = timezone.now() - timedelta(seconds=settings.STK_TIMEOUT_SECONDS)
    with chama_context(chama_id):
        stale = list(
            StkRequest.objects.filter(status=StkStatus.PENDING, created_at__lt=cutoff)
            .order_by("created_at")
            .values_list("id", "checkout_request_id")[:200]
        )
        linked = active_credentials() if stale and gateway_configured() else None
    swept = 0
    for request_id, checkout in stale:
        result = None
        if linked is not None and checkout:
            try:
                result = get_gateway().query_stk(linked[0], checkout)
            except GatewayError:
                result = None
        with chama_context(chama_id):
            if result is not None:
                apply_stk_result(result)
                continue
            swept += StkRequest.objects.filter(id=request_id, status=StkStatus.PENDING).update(
                status=StkStatus.TIMEOUT, completed_at=timezone.now()
            )
    return swept
