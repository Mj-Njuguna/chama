"""Read models for the app's home screens."""

import uuid
from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ledger.services import LedgerAccount, cash_balance, member_balance
from apps.organizations.services import (
    OFFICIALS,
    RequiresChama,
    Role,
    current_membership_id,
    current_role,
)
from core.api_errors import ErrorSerializer

CYCLE_STATUSES = ("paid", "due", "overdue", "none")


class HomeMemberSerializer(serializers.Serializer[Any]):
    savings_minor = serializers.IntegerField(allow_null=True)
    cycle_status = serializers.ChoiceField(choices=CYCLE_STATUSES, allow_null=True)
    cycle_due_date = serializers.DateField(allow_null=True)
    cycle_balance_minor = serializers.IntegerField(allow_null=True)
    active_loan_balance_minor = serializers.IntegerField(allow_null=True)


class HomeOfficialSerializer(serializers.Serializer[Any]):
    liquidity_minor = serializers.IntegerField(allow_null=True)
    collection_rate_percent = serializers.IntegerField(
        allow_null=True, min_value=0, max_value=100, help_text="0-100, computed by the server."
    )
    pending_approvals = serializers.IntegerField(allow_null=True)
    overdue_count = serializers.IntegerField(allow_null=True)


class ChamaHomeSerializer(serializers.Serializer[Any]):
    """Values are null until the module that computes them is live."""

    role = serializers.ChoiceField(choices=Role.choices)
    member = HomeMemberSerializer(allow_null=True)
    official = HomeOfficialSerializer(
        allow_null=True, help_text="Present only for officials (chair, treasurer, secretary)."
    )


def _member_section(member_id: uuid.UUID) -> dict[str, Any]:
    # Savings are credits on member_savings (negative); show them as a positive amount.
    savings = -member_balance(LedgerAccount.MEMBER_SAVINGS, member_id)
    owed = member_balance(LedgerAccount.LOANS_RECEIVABLE, member_id) + member_balance(
        LedgerAccount.INTEREST_RECEIVABLE, member_id
    )
    return {
        "savings_minor": savings,
        "cycle_status": None,  # contribution cycles: phase 2
        "cycle_due_date": None,
        "cycle_balance_minor": None,
        "active_loan_balance_minor": owed,
    }


def _official_section() -> dict[str, Any]:
    return {
        "liquidity_minor": cash_balance(),
        "collection_rate_percent": None,  # contribution cycles: phase 2
        "pending_approvals": None,  # loan and expense approvals: phases 3 and 4
        "overdue_count": None,  # contribution cycles: phase 2
    }


class ChamaHomeView(APIView):
    """The home dashboard of the active chama: the caller's own figures, plus group figures for
    officials."""

    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_home",
        responses={
            200: ChamaHomeSerializer,
            401: OpenApiResponse(ErrorSerializer, description="not_authenticated"),
            404: OpenApiResponse(ErrorSerializer, description="chama_not_available"),
        },
    )
    def get(self, request: Request) -> Response:
        role = current_role(request)
        member_id = current_membership_id(request)
        if role is None or member_id is None:
            raise NotAuthenticated()
        body = {
            "role": role.value,
            "member": _member_section(member_id),
            "official": _official_section() if role in OFFICIALS else None,
        }
        return Response(ChamaHomeSerializer(body).data)
