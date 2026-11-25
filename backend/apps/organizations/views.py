"""Organizations HTTP endpoints: me, chamas, constitution, members, invitations, changes."""

import uuid
from dataclasses import asdict
from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import SessionIdentity, chama_security_events
from apps.organizations.chamas import create_chama, me, switch_chama, update_display_name
from apps.organizations.constitution import EDITABLE_FIELDS, get_rules, update_rules
from apps.organizations.members import (
    accept,
    change_role,
    decline,
    import_roster,
    invite,
    members,
    preview_roster,
    remove,
)
from apps.organizations.models import (
    ContributionFrequency,
    InterestMethod,
    MembershipStatus,
    PaymentChannel,
    PenaltyType,
    PendingChangeKind,
    PendingChangeStatus,
)
from apps.organizations.pending import cancel_change, freeze_chama, open_changes
from apps.organizations.permissions import Action, RequiresAction, RequiresChama
from apps.organizations.roles import Role
from apps.organizations.selectors import current_role
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
ROLES = Role.choices


def _session(request: Request) -> SessionIdentity:
    """The verified session of an authenticated request (set by BearerSessionAuthentication)."""
    if not isinstance(request.auth, SessionIdentity):
        raise NotAuthenticated()
    return request.auth


# ---------------------------------------------------------------- me


class MeUserSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    phone = serializers.CharField(help_text="E.164, e.g. +254712345678.")
    display_name = serializers.CharField(allow_null=True, help_text="Null until set.")


class MeMembershipSerializer(serializers.Serializer[Any]):
    chama_id = serializers.UUIDField()
    chama_name = serializers.CharField()
    role = serializers.ChoiceField(choices=ROLES)
    status = serializers.ChoiceField(choices=MembershipStatus.choices)


class MeSerializer(serializers.Serializer[Any]):
    user = MeUserSerializer()
    session_id = serializers.UUIDField()
    session_started_at = serializers.DateTimeField(
        help_text="When this device's session began with a code sign-in (UTC)."
    )
    device_id = serializers.UUIDField()
    active_chama_id = serializers.UUIDField(
        allow_null=True,
        help_text="Null when no chama is chosen or the membership is no longer active.",
    )
    role = serializers.ChoiceField(
        choices=ROLES, allow_null=True, help_text="Role in the active chama."
    )
    member_code = serializers.CharField(
        allow_null=True, help_text="Your reference code in the active chama (M-Pesa account)."
    )
    memberships = MeMembershipSerializer(many=True)
    has_pin = serializers.BooleanField(help_text="Whether a server step-up PIN is set.")
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=[a.value for a in Action]),
        help_text="What the role allows in the active chama (empty without one). A display "
        "hint only, never authorization: a step-up may still be required.",
    )


class MeUpdateSerializer(serializers.Serializer[Any]):
    display_name = serializers.CharField(
        max_length=200,
        allow_blank=True,
        trim_whitespace=False,
        help_text="1 to 50 characters after trimming; no control or invisible characters.",
    )


def _me_body(request: Request) -> Any:
    found = me(_session(request))
    return MeSerializer(
        {
            "user": {
                "id": found.user_id,
                "phone": found.phone,
                "display_name": found.display_name,
            },
            "session_id": found.session_id,
            "session_started_at": found.session_started_at,
            "device_id": found.device_id,
            "active_chama_id": found.active_chama_id,
            "role": found.role,
            "member_code": found.member_code,
            "memberships": [asdict(m) for m in found.memberships],
            "has_pin": found.has_pin,
            "permissions": found.permissions,
        }
    ).data


class MeView(APIView):
    """The caller, their session and their memberships. Works with or without a chama."""

    chama_context = False  # reads the user's own memberships across chamas

    @extend_schema(operation_id="me", responses={200: MeSerializer, 401: _UNAUTHORIZED})
    def get(self, request: Request) -> Response:
        return Response(_me_body(request))

    @extend_schema(
        operation_id="me_update",
        request=MeUpdateSerializer,
        responses={200: MeSerializer, 400: _INVALID, 401: _UNAUTHORIZED},
    )
    def patch(self, request: Request) -> Response:
        """Set your own display name."""
        body = MeUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        update_display_name(_session(request), body.validated_data["display_name"])
        return Response(_me_body(request))


# ---------------------------------------------------------------- chamas


class ChamaSummarySerializer(serializers.Serializer[Any]):
    chama_id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=ROLES)
    status = serializers.ChoiceField(choices=MembershipStatus.choices)


class ChamaListSerializer(serializers.Serializer[Any]):
    chamas = ChamaSummarySerializer(many=True)
    active_chama_id = serializers.UUIDField(allow_null=True)


class CreateChamaSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=200, trim_whitespace=False)


class CreatedChamaSerializer(serializers.Serializer[Any]):
    chama_id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=ROLES)
    member_code = serializers.CharField()
    active_chama_id = serializers.UUIDField(help_text="The new chama; now active.")


class ChamasView(APIView):
    """Your chamas (for the chama switcher), and creating a new one."""

    chama_context = False

    @extend_schema(
        operation_id="chamas_list", responses={200: ChamaListSerializer, 401: _UNAUTHORIZED}
    )
    def get(self, request: Request) -> Response:
        found = me(_session(request))
        chamas = [
            {"chama_id": m.chama_id, "name": m.chama_name, "role": m.role, "status": m.status}
            for m in found.memberships
            if m.status in ("active", "invited")
        ]
        return Response(
            ChamaListSerializer({"chamas": chamas, "active_chama_id": found.active_chama_id}).data
        )

    @extend_schema(
        operation_id="chamas_create",
        request=CreateChamaSerializer,
        responses={
            201: CreatedChamaSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            429: OpenApiResponse(ErrorSerializer, description="rate_limited (per user per day)"),
        },
    )
    def post(self, request: Request) -> Response:
        """Create a chama. You become its chair and it becomes your active chama."""
        body = CreateChamaSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        created = create_chama(_session(request), body.validated_data["name"])
        return Response(
            CreatedChamaSerializer({**asdict(created), "active_chama_id": created.chama_id}).data,
            status=status.HTTP_201_CREATED,
        )


class SwitchChamaSerializer(serializers.Serializer[Any]):
    chama_id = serializers.UUIDField()


class SwitchedChamaSerializer(serializers.Serializer[Any]):
    active_chama_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=ROLES)


class SwitchChamaView(APIView):
    """Make one of your chamas the session's active chama."""

    chama_context = False

    @extend_schema(
        operation_id="auth_switch_chama",
        request=SwitchChamaSerializer,
        responses={
            200: SwitchedChamaSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            404: _NO_CHAMA,
        },
    )
    def post(self, request: Request) -> Response:
        body = SwitchChamaSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        chama_id = body.validated_data["chama_id"]
        role = switch_chama(_session(request), chama_id)
        return Response(SwitchedChamaSerializer({"active_chama_id": chama_id, "role": role}).data)


# ---------------------------------------------------------------- constitution


class ConstitutionSerializer(serializers.Serializer[Any]):
    contribution_frequency = serializers.ChoiceField(choices=ContributionFrequency.values)
    contribution_amount_minor = serializers.IntegerField(
        min_value=0, help_text="Base contribution per cycle, KES minor units (cents)."
    )
    due_day = serializers.IntegerField(
        min_value=0, help_text="Monthly: day of month 1-28. Weekly: 0 = Monday ... 6 = Sunday."
    )
    grace_days = serializers.IntegerField(min_value=0, max_value=30)
    penalty_type = serializers.ChoiceField(choices=PenaltyType.values)
    penalty_flat_minor = serializers.IntegerField(min_value=0, help_text="KES minor units.")
    penalty_rate_bp = serializers.IntegerField(
        min_value=0, max_value=10000, help_text="Basis points of the amount due (1% = 100)."
    )
    loan_multiplier = serializers.DecimalField(
        max_digits=4,
        decimal_places=1,
        min_value=0,
        max_value=20,
        help_text="Max loan = this x member savings.",
    )
    interest_method = serializers.ChoiceField(choices=InterestMethod.values)
    interest_rate_bp = serializers.IntegerField(
        min_value=0, max_value=10000, help_text="Per month, basis points."
    )
    max_loan_months = serializers.IntegerField(min_value=1, max_value=60)
    payment_channel = serializers.ChoiceField(
        choices=PaymentChannel.values,
        help_text="paybill is recommended: till payments carry no member code.",
    )


class ConstitutionOutSerializer(ConstitutionSerializer):
    version = serializers.IntegerField()
    updated_at = serializers.DateTimeField()
    updated_by_user_id = serializers.UUIDField(allow_null=True)


class ConstitutionView(APIView):
    """The active chama's rules. Every member reads; officials change with a fresh PIN."""

    def get_permissions(self) -> list[Any]:
        if self.request.method == "GET":
            return [RequiresAction.for_(Action.VIEW_CONSTITUTION)()]
        return [RequiresAction.for_(Action.EDIT_CONSTITUTION)()]

    @extend_schema(
        operation_id="chama_constitution",
        responses={200: ConstitutionOutSerializer, 401: _UNAUTHORIZED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        return Response(ConstitutionOutSerializer(asdict(get_rules(_session(request)))).data)

    @extend_schema(
        operation_id="chama_constitution_update",
        request=ConstitutionSerializer(partial=True),
        responses={
            200: ConstitutionOutSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
        },
    )
    def patch(self, request: Request) -> Response:
        body = ConstitutionSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        changes = {k: v for k, v in body.validated_data.items() if k in EDITABLE_FIELDS}
        rules = update_rules(_session(request), current_role(request), changes)
        return Response(ConstitutionOutSerializer(asdict(rules)).data)


# ---------------------------------------------------------------- members


class MemberSerializer(serializers.Serializer[Any]):
    membership_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=ROLES)
    status = serializers.ChoiceField(choices=MembershipStatus.choices)
    member_code = serializers.CharField()
    phone = serializers.CharField(allow_null=True, help_text="Officials only; null otherwise.")
    joined_at = serializers.DateTimeField(allow_null=True)


class MembersSerializer(serializers.Serializer[Any]):
    members = MemberSerializer(many=True)


class MembersView(APIView):
    """The roster. Officials also see invitations and phone numbers."""

    permission_classes = (RequiresAction.for_(Action.VIEW_MEMBERS),)

    @extend_schema(
        operation_id="chama_members",
        responses={200: MembersSerializer, 401: _UNAUTHORIZED, 403: _DENIED, 404: _NO_CHAMA},
    )
    def get(self, request: Request) -> Response:
        found = members(_session(request), current_role(request))
        return Response(MembersSerializer({"members": [asdict(m) for m in found]}).data)


class ChangeRoleSerializer(serializers.Serializer[Any]):
    role = serializers.ChoiceField(choices=ROLES)


class MemberView(APIView):
    """Change a member's role (chair, fresh OTP) or remove them (officials, fresh OTP)."""

    def get_permissions(self) -> list[Any]:
        if self.request.method == "PATCH":
            return [RequiresAction.for_(Action.CHANGE_ROLES)()]
        return [RequiresAction.for_(Action.MANAGE_MEMBERS)()]

    @extend_schema(
        operation_id="chama_member_update",
        request=ChangeRoleSerializer,
        responses={
            200: MemberSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: OpenApiResponse(ErrorSerializer, description="not_found, chama_not_available"),
        },
    )
    def patch(self, request: Request, membership_id: uuid.UUID) -> Response:
        body = ChangeRoleSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        changed = change_role(
            _session(request), current_role(request), membership_id, body.validated_data["role"]
        )
        return Response(MemberSerializer(asdict(changed)).data)

    @extend_schema(
        operation_id="chama_member_remove",
        responses={
            204: None,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: OpenApiResponse(ErrorSerializer, description="not_found, chama_not_available"),
        },
    )
    def delete(self, request: Request, membership_id: uuid.UUID) -> Response:
        remove(_session(request), current_role(request), membership_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InviteSerializer(serializers.Serializer[Any]):
    phone = serializers.CharField(max_length=32, help_text="Any accepted format.")
    name = serializers.CharField(max_length=200, trim_whitespace=False)
    role = serializers.ChoiceField(
        choices=ROLES, default=Role.MEMBER.value, help_text="Officials: chair only."
    )


class InvitationSerializer(serializers.Serializer[Any]):
    membership_id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=ROLES)
    status = serializers.ChoiceField(choices=MembershipStatus.choices)
    member_code = serializers.CharField()


class InvitationsView(APIView):
    """Invite one person by phone number. Officials, with a fresh OTP."""

    permission_classes = (RequiresAction.for_(Action.MANAGE_MEMBERS),)

    @extend_schema(
        operation_id="chama_invite",
        request=InviteSerializer,
        responses={
            201: InvitationSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
        },
    )
    def post(self, request: Request) -> Response:
        body = InviteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        created = invite(
            _session(request), current_role(request), data["phone"], data["name"], data["role"]
        )
        return Response(InvitationSerializer(asdict(created)).data, status=status.HTTP_201_CREATED)


class RosterImportSerializer(serializers.Serializer[Any]):
    csv = serializers.CharField(
        trim_whitespace=False,
        help_text="CSV text with a header row: name, phone, and optionally role.",
    )
    commit = serializers.BooleanField(
        default=False,
        help_text="false: check only (no step-up). true: invite every valid row (fresh OTP).",
    )


class RosterRowSerializer(serializers.Serializer[Any]):
    row = serializers.IntegerField(help_text="Line number in the file, header = 1.")
    name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    role = serializers.CharField()
    error = serializers.ChoiceField(
        choices=[
            "invalid_name",
            "invalid_phone",
            "invalid_role",
            "duplicate_in_file",
            "already_member",
        ],
        allow_null=True,
    )


class RosterResultSerializer(serializers.Serializer[Any]):
    rows = RosterRowSerializer(many=True)
    valid = serializers.IntegerField()
    invalid = serializers.IntegerField()
    invited = serializers.IntegerField(help_text="Invitations created (commit only).")


class RosterImportView(APIView):
    """Check or import a member roster (CSV). Officials."""

    permission_classes = (RequiresChama,)

    @extend_schema(
        operation_id="chama_members_import",
        request=RosterImportSerializer,
        responses={
            200: RosterResultSerializer,
            400: _INVALID,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: _NO_CHAMA,
        },
    )
    def post(self, request: Request) -> Response:
        body = RosterImportSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        identity, role = _session(request), current_role(request)
        text = body.validated_data["csv"]
        if body.validated_data["commit"]:
            if not RequiresAction.for_(Action.MANAGE_MEMBERS)().has_permission(request, self):
                raise PermissionDenied("Your role does not allow this.")
            result = import_roster(identity, role, text)
        else:
            result = preview_roster(identity, role, text)
        return Response(
            RosterResultSerializer(
                {
                    "rows": [asdict(r) for r in result.rows],
                    "valid": result.valid,
                    "invalid": result.invalid,
                    "invited": result.invited,
                }
            ).data
        )


# ---------------------------------------------------------------- invitations (invitee)


class AcceptSerializer(serializers.Serializer[Any]):
    accept_terms = serializers.BooleanField(help_text="Must be true: the invite terms.")
    scheduled_stk_opt_in = serializers.BooleanField(
        default=True,
        help_text="Receive an M-Pesa prompt on the morning a contribution is due.",
    )


class AcceptedSerializer(serializers.Serializer[Any]):
    chama_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=ROLES)
    member_code = serializers.CharField()


_NO_INVITATION = OpenApiResponse(
    ErrorSerializer, description="chama_not_available (no open invitation)"
)


class InvitationAcceptView(APIView):
    """Join a chama you were invited to. Switch to it afterwards to use it."""

    chama_context = False

    @extend_schema(
        operation_id="invitation_accept",
        request=AcceptSerializer,
        responses={200: AcceptedSerializer, 400: _INVALID, 401: _UNAUTHORIZED, 404: _NO_INVITATION},
    )
    def post(self, request: Request, chama_id: uuid.UUID) -> Response:
        body = AcceptSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        joined = accept(
            _session(request),
            chama_id,
            accept_terms=body.validated_data["accept_terms"],
            stk_opt_in=body.validated_data["scheduled_stk_opt_in"],
        )
        return Response(
            AcceptedSerializer(
                {"chama_id": chama_id, "role": joined.role, "member_code": joined.member_code}
            ).data
        )


class InvitationDeclineView(APIView):
    chama_context = False

    @extend_schema(
        operation_id="invitation_decline",
        request=None,
        responses={204: None, 401: _UNAUTHORIZED, 404: _NO_INVITATION},
    )
    def post(self, request: Request, chama_id: uuid.UUID) -> Response:
        decline(_session(request), chama_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- changes, freeze, audit


class PendingChangeSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=PendingChangeKind.values)
    status = serializers.ChoiceField(choices=PendingChangeStatus.choices)
    requested_by_user_id = serializers.UUIDField()
    requested_at = serializers.DateTimeField()
    effective_at = serializers.DateTimeField(help_text="Can be applied from this time.")
    expires_at = serializers.DateTimeField(help_text="Expires if not applied by this time.")


class PendingChangesSerializer(serializers.Serializer[Any]):
    changes = PendingChangeSerializer(many=True)


class FrozenSerializer(serializers.Serializer[Any]):
    frozen_at = serializers.DateTimeField()


class ChamaChangesView(APIView):
    """Open cooling-off changes of the active chama (officials)."""

    permission_classes = (RequiresAction.for_(Action.REVIEW_CHAMA_CHANGES),)

    @extend_schema(
        operation_id="chama_changes_list",
        responses={200: PendingChangesSerializer, 401: _UNAUTHORIZED, 403: _DENIED},
    )
    def get(self, request: Request) -> Response:
        changes = [asdict(change) for change in open_changes(_session(request))]
        return Response(PendingChangesSerializer({"changes": changes}).data)


class ChamaChangeCancelView(APIView):
    """Cancel an open change. Any official; no step-up, even while the chama is frozen."""

    permission_classes = (RequiresAction.for_(Action.REVIEW_CHAMA_CHANGES),)

    @extend_schema(
        operation_id="chama_changes_cancel",
        request=None,
        responses={
            200: PendingChangeSerializer,
            401: _UNAUTHORIZED,
            403: _DENIED,
            404: OpenApiResponse(ErrorSerializer, description="not_found (no open change)"),
        },
    )
    def post(self, request: Request, change_id: uuid.UUID) -> Response:
        return Response(PendingChangeSerializer(cancel_change(_session(request), change_id)).data)


class ChamaFreezeView(APIView):
    """ "This wasn't me": freeze the chama. Any official, no step-up; idempotent. Cancels every
    open change and alerts every official; only an operator can unfreeze, after review."""

    permission_classes = (RequiresAction.for_(Action.FREEZE_CHAMA),)

    @extend_schema(
        operation_id="chama_freeze",
        request=None,
        responses={200: FrozenSerializer, 401: _UNAUTHORIZED, 403: _DENIED},
    )
    def post(self, request: Request) -> Response:
        return Response(FrozenSerializer({"frozen_at": freeze_chama(_session(request))}).data)


class SecurityEventOutSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    occurred_at = serializers.DateTimeField()
    kind = serializers.CharField()
    user_id = serializers.UUIDField(allow_null=True)
    device_id = serializers.UUIDField(allow_null=True)
    metadata = serializers.DictField()


class SecurityEventsSerializer(serializers.Serializer[Any]):
    events = SecurityEventOutSerializer(many=True)


class ChamaSecurityEventsView(APIView):
    """The active chama's audit trail, newest first (officials)."""

    permission_classes = (RequiresAction.for_(Action.VIEW_SECURITY_EVENTS),)

    @extend_schema(
        operation_id="chama_security_events",
        responses={200: SecurityEventsSerializer, 401: _UNAUTHORIZED, 403: _DENIED},
    )
    def get(self, request: Request) -> Response:
        events = [asdict(e) for e in chama_security_events()]
        return Response(SecurityEventsSerializer({"events": events}).data)
