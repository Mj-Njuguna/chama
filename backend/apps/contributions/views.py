"""Contributions HTTP API: the current cycle, obligations, collection, statement, cashbook and
fine waivers. Members see their own figures; officials see everyone's."""

import uuid
from dataclasses import asdict
from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contributions.fines import approve_waiver, request_waiver, waivers
from apps.contributions.models import ObligationKind, ObligationStatus, WaiverStatus
from apps.contributions.summaries import collection_rows, current_cycle, obligations_for
from apps.ledger.services import LedgerAccount, cash_balance, cash_transactions
from apps.ledger.services import transactions_for_member as ledger_for_member
from apps.organizations.services import (
    OFFICIALS,
    Action,
    RequiresAction,
    RequiresChama,
    current_membership_id,
    current_role,
    member_names,
)
from core.api_errors import ApiError, ErrorSerializer

_UNAUTHORIZED = OpenApiResponse(ErrorSerializer, description="not_authenticated")
_NO_CHAMA = OpenApiResponse(ErrorSerializer, description="chama_not_available, not_found")
_DENIED = OpenApiResponse(
    ErrorSerializer,
    description="permission_denied, step_up_required, pin_not_set, chama_frozen",
)
_INVALID = OpenApiResponse(ErrorSerializer, description="validation_error")
_CONFLICT = OpenApiResponse(
    ErrorSerializer,
    description="state_conflict: fine_not_open, waiver_pending, waiver_decided, same_official",
)
_MEMBER_PARAM = OpenApiParameter(
    "member", str, description="`me` (default) or a membership id (officials only)."
)

# Member-facing ledger accounts on a statement; savings are shown as positive credits.
_STATEMENT_ACCOUNTS = {
    LedgerAccount.MEMBER_SAVINGS: -1,
    LedgerAccount.FINES_RECEIVABLE: 1,
    LedgerAccount.LOANS_RECEIVABLE: 1,
    LedgerAccount.INTEREST_RECEIVABLE: 1,
}


def _me(request: Request) -> uuid.UUID:
    member = current_membership_id(request)
    if member is None:
        raise NotAuthenticated()
    return member


def _chama(request: Request) -> uuid.UUID:
    chama = getattr(request.auth, "active_chama_id", None)
    if chama is None:
        raise NotAuthenticated()
    return uuid.UUID(str(chama))


def _target_member(request: Request) -> uuid.UUID:
    """``?member=me`` (default) or, for officials, another member of this chama."""
    raw = request.query_params.get("member", "me")
    me = _me(request)
    if raw in ("", "me"):
        return me
    try:
        member = uuid.UUID(raw)
    except ValueError:
        raise ValidationError({"member": ["invalid"]}) from None
    if member == me:
        return me
    if current_role(request) not in OFFICIALS:
        raise PermissionDenied()
    if member not in member_names([member]):
        raise ApiError("not_found", "No such member.", status_code=404)
    return member


def _not_found(what: str) -> ApiError:
    return ApiError("not_found", f"No {what}.", status_code=404)


# ---------------------------------------------------------------- cycle and obligations


class CycleSerializer(serializers.Serializer[Any]):
    cycle_id = serializers.UUIDField()
    period_start = serializers.DateField()
    due_date = serializers.DateField()
    grace_until = serializers.DateField()
    amount_minor = serializers.IntegerField()
    status = serializers.ChoiceField(choices=("open", "closed"))


class CurrentCycleView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_cycle_current",
        responses={200: CycleSerializer, 401: _UNAUTHORIZED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        cycle = current_cycle()
        if cycle is None:
            raise _not_found("contribution cycle yet")
        return Response(
            CycleSerializer(
                {
                    "cycle_id": cycle.id,
                    "period_start": cycle.period_start,
                    "due_date": cycle.due_date,
                    "grace_until": cycle.grace_until,
                    "amount_minor": cycle.amount_minor,
                    "status": cycle.status,
                }
            ).data
        )


class ObligationSerializer(serializers.Serializer[Any]):
    obligation_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=ObligationKind.choices)
    due_date = serializers.DateField()
    amount_minor = serializers.IntegerField()
    paid_minor = serializers.IntegerField()
    balance_minor = serializers.IntegerField()
    status = serializers.ChoiceField(choices=ObligationStatus.choices)


class ObligationListSerializer(serializers.Serializer[Any]):
    obligations = ObligationSerializer(many=True)


class ObligationsView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_obligations",
        parameters=[_MEMBER_PARAM],
        responses={200: ObligationListSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        rows = [asdict(o) for o in obligations_for(_target_member(request))]
        return Response(ObligationListSerializer({"obligations": rows}).data)


# ---------------------------------------------------------------- collection (officials)


class CollectionRowSerializer(serializers.Serializer[Any]):
    member_id = serializers.UUIDField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=("paid", "partial", "unpaid", "overdue"))
    balance_minor = serializers.IntegerField()


class CollectionSerializer(serializers.Serializer[Any]):
    cycle_id = serializers.UUIDField(allow_null=True)
    members = CollectionRowSerializer(many=True)


class CollectionView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.VIEW_GROUP_TOTALS)()]

    @extend_schema(
        operation_id="chama_collection",
        responses={200: CollectionSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        cycle = current_cycle()
        if cycle is None:
            return Response(CollectionSerializer({"cycle_id": None, "members": []}).data)
        rows = collection_rows(cycle)
        names = member_names([r.member_id for r in rows])
        body = [{**asdict(r), "name": names.get(r.member_id, "")} for r in rows]
        body.sort(key=lambda r: (r["status"] == "paid", -r["balance_minor"], r["name"]))
        return Response(CollectionSerializer({"cycle_id": cycle.id, "members": body}).data)


# ---------------------------------------------------------------- statement and cashbook


class StatementEntrySerializer(serializers.Serializer[Any]):
    transaction_id = serializers.UUIDField()
    kind = serializers.CharField()
    memo = serializers.CharField()
    created_at = serializers.DateTimeField()
    account = serializers.ChoiceField(choices=[a.value for a in _STATEMENT_ACCOUNTS])
    amount_minor = serializers.IntegerField(
        help_text="Savings: positive adds to savings. Fines and loans: positive adds to what "
        "the member owes."
    )


class StatementSerializer(serializers.Serializer[Any]):
    member_id = serializers.UUIDField()
    entries = StatementEntrySerializer(many=True)


class StatementView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_statement",
        parameters=[_MEMBER_PARAM],
        responses={200: StatementSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        member = _target_member(request)
        entries = []
        for txn in ledger_for_member(member, limit=100):
            for line in txn.lines:
                sign = _STATEMENT_ACCOUNTS.get(LedgerAccount(line.ledger_account))
                if sign is None or line.member_id != member:
                    continue
                entries.append(
                    {
                        "transaction_id": txn.transaction_id,
                        "kind": txn.kind,
                        "memo": txn.memo,
                        "created_at": txn.created_at,
                        "account": line.ledger_account,
                        "amount_minor": sign * line.amount_minor,
                    }
                )
        return Response(StatementSerializer({"member_id": member, "entries": entries}).data)


class CashbookEntrySerializer(serializers.Serializer[Any]):
    transaction_id = serializers.UUIDField()
    kind = serializers.CharField()
    memo = serializers.CharField()
    created_at = serializers.DateTimeField()
    member_id = serializers.UUIDField(allow_null=True)
    cash_minor = serializers.IntegerField(help_text="Money in (positive) or out (negative).")


class CashbookSerializer(serializers.Serializer[Any]):
    balance_minor = serializers.IntegerField()
    transactions = CashbookEntrySerializer(many=True)


class CashbookView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.VIEW_GROUP_TOTALS)()]

    @extend_schema(
        operation_id="chama_cashbook",
        responses={200: CashbookSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        rows = [asdict(t) for t in cash_transactions()]
        return Response(
            CashbookSerializer({"balance_minor": cash_balance(), "transactions": rows}).data
        )


# ---------------------------------------------------------------- fine waivers


class WaiverSerializer(serializers.Serializer[Any]):
    waiver_id = serializers.UUIDField()
    obligation_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    reason = serializers.CharField()
    status = serializers.ChoiceField(choices=WaiverStatus.choices)
    requested_by_member_id = serializers.UUIDField()
    approved_by_member_id = serializers.UUIDField(allow_null=True)
    amount_minor = serializers.IntegerField(help_text="The fine's open balance to waive.")
    created_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)


class WaiverListSerializer(serializers.Serializer[Any]):
    waivers = WaiverSerializer(many=True)


class WaiverRequestSerializer(serializers.Serializer[Any]):
    reason = serializers.CharField(max_length=200)


class FineWaiveView(APIView):
    """Request a waiver of a fine (an official, fresh PIN). A different official approves."""

    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.WAIVE_FINE)()]

    @extend_schema(
        operation_id="chama_fine_waive",
        request=WaiverRequestSerializer,
        responses={
            201: WaiverSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
            409: _CONFLICT,
        },
    )
    def post(self, request: Request, obligation_id: uuid.UUID) -> Response:
        body = WaiverRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        waiver = request_waiver(
            _chama(request), obligation_id, _me(request), body.validated_data["reason"]
        )
        return Response(WaiverSerializer(asdict(waiver)).data, status=status.HTTP_201_CREATED)


class WaiverApproveView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.WAIVE_FINE)()]

    @extend_schema(
        operation_id="chama_waiver_approve",
        request=None,
        responses={
            200: WaiverSerializer,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
            409: _CONFLICT,
        },
    )
    def post(self, request: Request, waiver_id: uuid.UUID) -> Response:
        waiver = approve_waiver(_chama(request), waiver_id, _me(request))
        return Response(WaiverSerializer(asdict(waiver)).data)


class WaiversView(APIView):
    def get_permissions(self) -> list[Any]:
        return [RequiresAction.for_(Action.VIEW_GROUP_TOTALS)()]

    @extend_schema(
        operation_id="chama_waivers",
        parameters=[OpenApiParameter("status", str, enum=WaiverStatus.values, required=False)],
        responses={200: WaiverListSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        wanted = request.query_params.get("status") or None
        if wanted is not None and wanted not in WaiverStatus.values:
            raise ValidationError({"status": ["invalid"]})
        rows = [asdict(w) for w in waivers(wanted)]
        return Response(WaiverListSerializer({"waivers": rows}).data)
