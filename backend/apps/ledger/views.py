"""Ledger HTTP API: manual receipts (officials)."""

from dataclasses import asdict
from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import SessionIdentity
from apps.ledger.models import ReceiptChannel
from apps.ledger.receipts import RECEIPT_MAX_MINOR, recent_receipts, record_receipt
from apps.organizations.services import Action, RequiresAction, current_membership_id
from core.api_errors import ErrorSerializer

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
_CONFLICT = OpenApiResponse(
    ErrorSerializer, description="state_conflict: duplicate_receipt, member_not_active"
)


def _session(request: Request) -> SessionIdentity:
    if not isinstance(request.auth, SessionIdentity) or request.auth.active_chama_id is None:
        raise NotAuthenticated()
    return request.auth


class ReceiptInSerializer(serializers.Serializer[Any]):
    channel = serializers.ChoiceField(choices=ReceiptChannel.choices)
    reference = serializers.CharField(max_length=60, help_text="Bank slip number or M-Pesa code.")
    amount_minor = serializers.IntegerField(min_value=1, max_value=RECEIPT_MAX_MINOR)
    received_on = serializers.DateField(help_text="Date on the slip (Africa/Nairobi).")
    member_id = serializers.UUIDField(
        required=False, allow_null=True, help_text="Omit to leave it for the resolver."
    )
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class ReceiptOutSerializer(serializers.Serializer[Any]):
    receipt_id = serializers.UUIDField()
    channel = serializers.ChoiceField(choices=ReceiptChannel.choices)
    reference = serializers.CharField()
    amount_minor = serializers.IntegerField()
    received_on = serializers.DateField()
    member_id = serializers.UUIDField(allow_null=True)
    note = serializers.CharField()
    recorded_by_member_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()


class ReceiptListSerializer(serializers.Serializer[Any]):
    receipts = ReceiptOutSerializer(many=True)


class ReceiptsView(APIView):
    """Manual receipts of the active chama. Officials list them; recording needs a fresh PIN."""

    def get_permissions(self) -> list[Any]:
        if self.request.method == "GET":
            return [RequiresAction.for_(Action.VIEW_GROUP_TOTALS)()]
        return [RequiresAction.for_(Action.RECORD_RECEIPT)()]

    @extend_schema(
        operation_id="chama_receipts",
        responses={200: ReceiptListSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        rows = [asdict(r) for r in recent_receipts()]
        return Response(ReceiptListSerializer({"receipts": rows}).data)

    @extend_schema(
        operation_id="chama_receipt_create",
        request=ReceiptInSerializer,
        responses={
            201: ReceiptOutSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
            409: _CONFLICT,
        },
    )
    def post(self, request: Request) -> Response:
        session = _session(request)
        body = ReceiptInSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        recorder = current_membership_id(request)
        if recorder is None or session.active_chama_id is None:
            raise NotAuthenticated()
        data = body.validated_data
        receipt = record_receipt(
            chama_id=session.active_chama_id,
            recorded_by_member_id=recorder,
            channel=data["channel"],
            reference=data["reference"],
            amount_minor=data["amount_minor"],
            received_on=data["received_on"],
            member_id=data.get("member_id"),
            note=data.get("note", ""),
        )
        return Response(ReceiptOutSerializer(asdict(receipt)).data, status=status.HTTP_201_CREATED)
