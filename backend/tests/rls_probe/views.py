"""Test-only endpoints. ``records`` is written the right way (chama from the session);
``naive_create`` deliberately trusts the request body, to show RLS stops that bug too."""

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.services import Action, RequiresAction, RequiresChama
from core.lists import ListSpec
from core.tenancy import AUTHENTICATED_CHAMA_ATTR, AUTHENTICATED_USER_ATTR, current_chama_id
from tests.rls_probe.models import ProbeRecord


@csrf_exempt
def records(request: HttpRequest) -> JsonResponse:
    if request.method == "POST":
        body = json.loads(request.body or b"{}")
        ProbeRecord.objects.create(
            chama_id=getattr(request, AUTHENTICATED_CHAMA_ATTR), name=body["name"]
        )
    context = current_chama_id()
    return JsonResponse(
        {
            "current_chama": str(context) if context else None,
            "names": sorted(ProbeRecord.objects.values_list("name", flat=True)),
        }
    )


@csrf_exempt
def naive_create(request: HttpRequest) -> JsonResponse:
    body = json.loads(request.body)
    ProbeRecord.objects.create(chama_id=body["chama_id"], name=body["name"])
    return JsonResponse({"created": True}, status=201)


@csrf_exempt
def create_then_fail(request: HttpRequest) -> JsonResponse:
    ProbeRecord.objects.create(chama_id=getattr(request, AUTHENTICATED_CHAMA_ATTR), name="x")
    raise RuntimeError("boom after a write")


def my_memberships(request: HttpRequest) -> JsonResponse:
    """Written the right way: the user comes only from the authenticated session."""
    from apps.organizations.services import memberships_for_user

    user_id = getattr(request, AUTHENTICATED_USER_ATTR)
    if user_id is None:
        return JsonResponse({"detail": "not authenticated"}, status=401)
    rows = memberships_for_user(user_id)
    return JsonResponse({"memberships": [[str(m.chama_id), m.role, m.status] for m in rows]})


class ChamaTotalsProbe(APIView):
    """Stands in for any role-gated endpoint without step-up (group totals).

    Step-up actions (receipts, members, export...) are tested with real sessions in
    tests/test_step_up.py; this fake middleware has no session to be fresh.
    """

    authentication_classes = ()  # identity comes from the middleware
    permission_classes = (RequiresAction.for_(Action.VIEW_GROUP_TOTALS),)

    def post(self, request: Request) -> Response:
        return Response({"ok": True})


# ---------------------------------------------------------------- list standard


def _probe_list_spec() -> ListSpec:
    return ListSpec(
        endpoint="probe.records",
        sorts={"name": ("name",)},
        default_sort="name",
        filters={},
        search=lambda qs, term: qs.filter(name__istartswith=term),
    )


class RecordList(APIView):
    """A list endpoint written to the list standard, over the probe table (RLS only)."""

    authentication_classes = ()  # identity comes from the middleware
    permission_classes = (RequiresChama,)

    def get(self, request: Request) -> Response:
        from core.lists import page_body, paginate

        page = paginate(request.query_params, ProbeRecord.objects.all(), _probe_list_spec())
        return Response(page_body(page, lambda r: {"id": str(r.id), "name": r.name}))


class OtherList(APIView):
    """Same rows, another endpoint name: a cursor from one must not work on the other."""

    authentication_classes = ()
    permission_classes = (RequiresChama,)

    def get(self, request: Request) -> Response:
        from dataclasses import replace

        from core.lists import page_body, paginate

        spec = replace(_probe_list_spec(), endpoint="probe.other")
        page = paginate(request.query_params, ProbeRecord.objects.all(), spec)
        return Response(page_body(page, lambda r: {"id": str(r.id), "name": r.name}))
