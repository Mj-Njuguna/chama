"""Payments HTTP API: provider callbacks (no session) and the app's endpoints."""

import json
import uuid
from dataclasses import asdict
from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated, NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import SessionIdentity, rate_limited
from apps.organizations.services import (
    Action,
    RequiresAction,
    RequiresChama,
    current_membership_id,
    current_role,
    is_allowed,
)
from apps.payments.confirm import allocated_member, resolve_payment, suggestions_for
from apps.payments.linking import apply_linking, chama_for_token, linking_view, save_linking
from apps.payments.models import (
    CallbackEvent,
    CallbackKind,
    LinkingChannel,
    LinkingEnvironment,
    Payment,
    PaymentAllocation,
    StkStatus,
)
from apps.payments.stk import own_stk_request, request_stk_push
from core.api_errors import ErrorSerializer
from core.ratelimit import Limit, RateLimitUnavailable, hit
from core.tenancy import chama_context

MAX_CALLBACK_BYTES = 16 * 1024
LINKING_STATES = ("not_linked", "pending", "active")
PAYMENT_STATES = ("allocated", "unallocated")
# Daraja's documented validation acceptance; also returned to confirmations and STK results
# (their expected response is not documented, see apps/payments/daraja.py).
ACCEPTED = {"ResultCode": "0", "ResultDesc": "Accepted"}
STK_LIMIT = Limit("stk_member", 5, 600)
STK_MIN_MINOR = 100  # KES 1
STK_MAX_MINOR = 15_000_000  # KES 150,000

_UNAUTHORIZED = OpenApiResponse(
    ErrorSerializer,
    description="not_authenticated, invalid_token, token_expired, session_revoked, session_expired",
)
_NO_CHAMA = OpenApiResponse(ErrorSerializer, description="chama_not_available")
_DENIED = OpenApiResponse(
    ErrorSerializer,
    description="permission_denied, step_up_required, pin_not_set, chama_frozen, cooling_off",
)
_INVALID = OpenApiResponse(ErrorSerializer, description="validation_error")
_NOT_FOUND = OpenApiResponse(ErrorSerializer, description="not_found")


def _session(request: Request) -> SessionIdentity:
    if not isinstance(request.auth, SessionIdentity) or request.auth.active_chama_id is None:
        raise NotAuthenticated()
    return request.auth


def _chama(session: SessionIdentity) -> uuid.UUID:
    if session.active_chama_id is None:
        raise NotAuthenticated()
    return session.active_chama_id


def _member(request: Request) -> uuid.UUID:
    member = current_membership_id(request)
    if member is None:
        raise NotAuthenticated()
    return member


# ---------------------------------------------------------------- provider callbacks


def _store_callback(token: str, kind: str, request: HttpRequest) -> HttpResponse | None:
    """Persist the raw body under the token's chama and queue processing. Returns an error
    response, or None when stored."""
    chama_id = chama_for_token(token)
    if chama_id is None:
        return HttpResponse(status=404)
    if len(request.body) > MAX_CALLBACK_BYTES:
        return HttpResponse(status=413)
    try:
        body: Any = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        body = {"_unparsed": request.body[:4000].decode("utf-8", "replace")}
    with chama_context(chama_id):
        event = CallbackEvent.objects.create(chama_id=chama_id, kind=kind, body=body)
        if kind != CallbackKind.C2B_VALIDATION:
            from apps.payments.tasks import process_callback

            event_id, chama = str(event.id), str(chama_id)
            transaction.on_commit(lambda: process_callback.delay(chama_id=chama, event_id=event_id))
    return None


@csrf_exempt
@require_POST
def c2b_validation(request: HttpRequest, token: str) -> HttpResponse:
    return _store_callback(token, CallbackKind.C2B_VALIDATION, request) or JsonResponse(ACCEPTED)


@csrf_exempt
@require_POST
def c2b_confirmation(request: HttpRequest, token: str) -> HttpResponse:
    return _store_callback(token, CallbackKind.C2B_CONFIRMATION, request) or JsonResponse(ACCEPTED)


@csrf_exempt
@require_POST
def stk_result(request: HttpRequest, token: str) -> HttpResponse:
    return _store_callback(token, CallbackKind.STK_RESULT, request) or JsonResponse(ACCEPTED)


# ---------------------------------------------------------------- payment linking


class LinkingOutSerializer(serializers.Serializer[Any]):
    channel = serializers.ChoiceField(choices=LinkingChannel.choices, allow_null=True)
    shortcode = serializers.CharField(allow_null=True)
    environment = serializers.ChoiceField(choices=LinkingEnvironment.choices, allow_null=True)
    credentials_set = serializers.BooleanField()
    status = serializers.ChoiceField(choices=LINKING_STATES)
    pending_change_id = serializers.UUIDField(allow_null=True)
    pending_shortcode = serializers.CharField(allow_null=True)


class LinkingInSerializer(serializers.Serializer[Any]):
    channel = serializers.ChoiceField(choices=LinkingChannel.choices)
    shortcode = serializers.RegexField(r"^[0-9]{5,12}$")
    environment = serializers.ChoiceField(choices=LinkingEnvironment.choices)
    consumer_key = serializers.CharField(max_length=200, write_only=True)
    consumer_secret = serializers.CharField(max_length=200, write_only=True)
    passkey = serializers.CharField(max_length=200, write_only=True)


class PaymentLinkingView(APIView):
    """The chama's own paybill or till. Officials read it; changing it needs a fresh OTP, and
    replacing active details waits out a cooling-off."""

    def get_permissions(self) -> list[Any]:
        if self.request.method == "GET":
            return [RequiresChama()]
        return [RequiresAction.for_(Action.PAYMENT_LINKING)()]

    @extend_schema(
        operation_id="chama_payment_linking",
        responses={200: LinkingOutSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        if not is_allowed(current_role(request), Action.VIEW_GROUP_TOTALS):
            raise NotFound()
        return Response(LinkingOutSerializer(asdict(linking_view(_session(request)))).data)

    @extend_schema(
        operation_id="chama_payment_linking_update",
        request=LinkingInSerializer,
        responses={
            200: LinkingOutSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
        },
    )
    def put(self, request: Request) -> Response:
        session = _session(request)
        body = LinkingInSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        view = save_linking(session, current_role(request), _chama(session), **body.validated_data)
        return Response(LinkingOutSerializer(asdict(view)).data)


class PaymentLinkingApplyView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.PAYMENT_LINKING)()]

    @extend_schema(
        operation_id="chama_payment_linking_apply",
        request=None,
        responses={200: LinkingOutSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NOT_FOUND},
    )
    def post(self, request: Request) -> Response:
        session = _session(request)
        view = apply_linking(session, current_role(request), _chama(session))
        return Response(LinkingOutSerializer(asdict(view)).data)


# ---------------------------------------------------------------- STK prompts


class StkInSerializer(serializers.Serializer[Any]):
    amount_minor = serializers.IntegerField(
        min_value=STK_MIN_MINOR,
        max_value=STK_MAX_MINOR,
        help_text="Whole shillings only (a multiple of 100).",
    )

    def validate_amount_minor(self, value: int) -> int:
        if value % 100:
            raise serializers.ValidationError("whole_shillings_only")
        return value


class StkRequestSerializer(serializers.Serializer[Any]):
    stk_request_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=StkStatus.choices)
    amount_minor = serializers.IntegerField()
    mpesa_receipt = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class StkRequestsView(APIView):
    """Prompt the caller's own phone to pay the chama (M-Pesa Express)."""

    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_payments_stk_create",
        request=StkInSerializer,
        parameters=[
            OpenApiParameter(
                "Idempotency-Key",
                str,
                OpenApiParameter.HEADER,
                required=False,
                description="Repeat a request safely: the same key returns the same prompt.",
            )
        ],
        responses={
            202: StkRequestSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            404: _NO_CHAMA,
            409: OpenApiResponse(
                ErrorSerializer, description="state_conflict: payments_not_linked"
            ),
            429: OpenApiResponse(ErrorSerializer, description="rate_limited"),
            503: OpenApiResponse(ErrorSerializer, description="service_unavailable"),
        },
    )
    def post(self, request: Request) -> Response:
        session = _session(request)
        member = _member(request)
        body = StkInSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        key = (request.headers.get("Idempotency-Key") or "").strip()[:80]
        if not key:
            try:
                verdict = hit(STK_LIMIT, str(member))
            except RateLimitUnavailable:
                verdict = None
            if verdict is not None and not verdict.allowed:
                raise rate_limited(verdict.retry_after)
            key = uuid.uuid4().hex
        view = request_stk_push(
            _chama(session),
            member_id=member,
            amount_minor=body.validated_data["amount_minor"],
            reason="member",
            idempotency_key=f"member:{member}:{key}",
        )
        return Response(StkRequestSerializer(asdict(view)).data, status=status.HTTP_202_ACCEPTED)


class StkRequestView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_payments_stk_status",
        responses={200: StkRequestSerializer, 401: _UNAUTHORIZED, 404: _NOT_FOUND},
    )
    def get(self, request: Request, stk_request_id: uuid.UUID) -> Response:
        found = own_stk_request(stk_request_id, _member(request))
        if found is None:
            raise NotFound()
        return Response(StkRequestSerializer(asdict(found)).data)


# ---------------------------------------------------------------- payments and the resolver


class SuggestionSerializer(serializers.Serializer[Any]):
    member_id = serializers.UUIDField()
    reason = serializers.ChoiceField(choices=("similar_code", "phone_pattern"))


class PaymentSerializer(serializers.Serializer[Any]):
    payment_id = serializers.UUIDField()
    member_id = serializers.UUIDField(allow_null=True)
    amount_minor = serializers.IntegerField()
    mpesa_receipt = serializers.CharField()
    received_at = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=PAYMENT_STATES)

    def get_fields(self) -> dict[str, serializers.Field[Any, Any, Any, Any]]:
        # "source" is also an attribute of every DRF field, so it is added here rather than
        # declared on the class.
        fields = super().get_fields()
        fields["source"] = serializers.ChoiceField(choices=("c2b", "stk"))
        return fields


class UnallocatedPaymentSerializer(PaymentSerializer):
    payer_masked = serializers.CharField()
    bill_ref = serializers.CharField()
    suggestions = SuggestionSerializer(many=True)


class PaymentListSerializer(serializers.Serializer[Any]):
    payments = PaymentSerializer(many=True)


class UnallocatedListSerializer(serializers.Serializer[Any]):
    payments = UnallocatedPaymentSerializer(many=True)


def _row(payment: Payment, owners: dict[uuid.UUID, uuid.UUID]) -> dict[str, Any]:
    owner = owners.get(payment.id)
    return {
        "payment_id": payment.id,
        "member_id": owner,
        "amount_minor": payment.amount_minor,
        "mpesa_receipt": payment.mpesa_receipt,
        "received_at": payment.received_at,
        "source": payment.source,
        "status": "allocated" if owner else "unallocated",
    }


class PaymentsView(APIView):
    """M-Pesa payments: a member sees their own; officials see every payment (``mine=true``
    narrows it to their own)."""

    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_payments",
        parameters=[OpenApiParameter("mine", bool, required=False)],
        responses={200: PaymentListSerializer, 401: _UNAUTHORIZED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        member = _member(request)
        official = is_allowed(current_role(request), Action.VIEW_GROUP_TOTALS)
        mine = request.query_params.get("mine", "").lower() in ("1", "true", "yes")
        owners = dict(PaymentAllocation.objects.values_list("payment_id", "member_id"))
        rows = Payment.objects.order_by("-received_at", "-id")
        if mine or not official:
            rows = rows.filter(id__in=[p for p, m in owners.items() if m == member])
        data = [_row(p, owners) for p in rows[:100]]
        return Response(PaymentListSerializer({"payments": data}).data)


class UnallocatedPaymentsView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_payments_unallocated",
        responses={200: UnallocatedListSerializer, 401: _UNAUTHORIZED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        if not is_allowed(current_role(request), Action.VIEW_GROUP_TOTALS):
            raise NotFound()
        allocated = PaymentAllocation.objects.values_list("payment_id", flat=True)
        rows = Payment.objects.exclude(id__in=allocated).order_by("received_at", "id")[:100]
        data = [
            {
                **_row(p, {}),
                "payer_masked": p.payer_masked,
                "bill_ref": p.bill_ref,
                "suggestions": [asdict(s) for s in suggestions_for(p)],
            }
            for p in rows
        ]
        return Response(UnallocatedListSerializer({"payments": data}).data)


class AllocateInSerializer(serializers.Serializer[Any]):
    member_id = serializers.UUIDField()


class AllocatePaymentView(APIView):
    """Assign an unallocated payment to a member (officials, fresh PIN)."""

    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.ALLOCATE_PAYMENT)()]

    @extend_schema(
        operation_id="chama_payment_allocate",
        request=AllocateInSerializer,
        responses={
            200: PaymentSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NOT_FOUND,
            409: OpenApiResponse(
                ErrorSerializer,
                description="state_conflict: payment_already_allocated, member_not_active",
            ),
        },
    )
    def post(self, request: Request, payment_id: uuid.UUID) -> Response:
        session = _session(request)
        body = AllocateInSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            payment = resolve_payment(
                _chama(session),
                payment_id,
                body.validated_data["member_id"],
                _member(request),
            )
        except LookupError:
            raise NotFound() from None
        owner = allocated_member(payment.id)
        owners = {payment.id: owner} if owner else {}
        return Response(PaymentSerializer(_row(payment, owners)).data)
