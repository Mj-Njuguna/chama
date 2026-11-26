"""Organizations routes, all under /api/v1/."""

from django.urls import path

from apps.organizations.views import (
    ChamaChangeCancelView,
    ChamaChangesView,
    ChamaFreezeView,
    ChamaSecurityEventsView,
    ChamasView,
    ConstitutionView,
    InvitationAcceptView,
    InvitationDeclineView,
    InvitationsView,
    MembersView,
    MemberView,
    MeView,
    MyMembershipView,
    RosterImportView,
    SwitchChamaView,
)

urlpatterns = [
    path("auth/switch-chama", SwitchChamaView.as_view(), name="switch-chama"),
    path("me", MeView.as_view(), name="me"),
    path("chamas", ChamasView.as_view(), name="chamas"),
    path("chama/constitution", ConstitutionView.as_view(), name="chama-constitution"),
    path("chama/members", MembersView.as_view(), name="chama-members"),
    path("chama/membership", MyMembershipView.as_view(), name="chama-membership"),
    path("chama/members/import", RosterImportView.as_view(), name="chama-members-import"),
    path("chama/members/<uuid:membership_id>", MemberView.as_view(), name="chama-member"),
    path("chama/invitations", InvitationsView.as_view(), name="chama-invitations"),
    path("chama/changes", ChamaChangesView.as_view(), name="chama-changes"),
    path(
        "chama/changes/<uuid:change_id>/cancel",
        ChamaChangeCancelView.as_view(),
        name="chama-change-cancel",
    ),
    path("chama/freeze", ChamaFreezeView.as_view(), name="chama-freeze"),
    path("chama/security-events", ChamaSecurityEventsView.as_view(), name="chama-security-events"),
    path(
        "invitations/<uuid:chama_id>/accept",
        InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
    path(
        "invitations/<uuid:chama_id>/decline",
        InvitationDeclineView.as_view(),
        name="invitation-decline",
    ),
]
