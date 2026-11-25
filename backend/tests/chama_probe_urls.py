"""The real routes plus probe endpoints that need a chama.

Every route goes through the real middleware (session check, membership check, chama
context); the probes stand in for chama endpoints of later phases.
"""

from typing import Any

from django.urls import path
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations import pending
from apps.organizations.services import Action, RequiresAction, RequiresChama, current_role
from apps.organizations.views import PendingChangeSerializer, _session
from config.urls import urlpatterns as real_urlpatterns
from core.tenancy import current_chama_id


class ChamaDataProbe(APIView):
    permission_classes = (RequiresChama,)

    def get(self, request: Request) -> Response:
        return Response({"chama": str(current_chama_id())})


class TotalsProbe(APIView):
    permission_classes = (RequiresAction.for_(Action.VIEW_GROUP_TOTALS),)

    def get(self, request: Request) -> Response:
        return Response({"chama": str(current_chama_id())})


def _action_probe(action: Action) -> Any:
    class ActionProbe(APIView):
        permission_classes = (RequiresAction.for_(action),)

        def post(self, request: Request) -> Response:
            return Response({"action": action.value})

    return ActionProbe.as_view()


def _change_probes(kind: str, action: Action) -> list[Any]:
    """Stand-ins for the payment-linking endpoints: request and apply a cooling-off change
    through the real policy and the pending module."""

    class RequestProbe(APIView):
        permission_classes = (RequiresAction.for_(action),)

        def post(self, request: Request) -> Response:
            change = pending.request_change(_session(request), current_role(request), kind)
            return Response(PendingChangeSerializer(change).data, status=201)

    class ApplyProbe(APIView):
        permission_classes = (RequiresAction.for_(action),)

        def post(self, request: Request, change_id: str) -> Response:
            change = pending.apply_change(_session(request), current_role(request), change_id)
            return Response(PendingChangeSerializer(change).data)

    return [
        path(f"probe/change/{kind}", RequestProbe.as_view()),
        path(f"probe/change/{kind}/<uuid:change_id>/apply", ApplyProbe.as_view()),
    ]


urlpatterns: list[Any] = [
    *real_urlpatterns,
    path("probe/chama-data", ChamaDataProbe.as_view()),
    path("probe/totals", TotalsProbe.as_view()),
    # One probe per action, for the step-up policy (tests/test_step_up.py).
    *[path(f"probe/action/{action.value}", _action_probe(action)) for action in Action],
    *_change_probes("payment_details", Action.PAYMENT_LINKING),
]
