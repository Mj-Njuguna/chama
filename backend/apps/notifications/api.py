"""Notifications HTTP API: the chama feed and the WhatsApp status webhook."""

import json
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import FeedEvent
from apps.notifications.whatsapp import signature_valid
from apps.organizations.services import RequiresChama
from core.api_errors import ErrorSerializer
from core.lists import ListSpec, paginate

FEED_LIST = ListSpec(
    endpoint="chama-feed", sorts={"created": ("created_at",)}, default_sort="-created"
)


class FeedEventSerializer(serializers.Serializer[Any]):
    event_id = serializers.UUIDField(source="id")
    kind = serializers.CharField()
    created_at = serializers.DateTimeField()
    payload = serializers.DictField()


class FeedPageSerializer(serializers.Serializer[Any]):
    events = FeedEventSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class FeedView(APIView):
    """The active chama's activity feed, newest first (every member)."""

    def get_permissions(self) -> list[Any]:
        return [RequiresChama()]

    @extend_schema(
        operation_id="chama_feed",
        parameters=[
            OpenApiParameter("cursor", str, description="next_cursor from the previous page"),
            OpenApiParameter("limit", int, description="Page size (capped by the server)"),
        ],
        responses={
            200: FeedPageSerializer,
            400: OpenApiResponse(ErrorSerializer, description="validation_error"),
            401: OpenApiResponse(ErrorSerializer, description="not_authenticated"),
            404: OpenApiResponse(ErrorSerializer, description="chama_not_available"),
        },
    )
    def get(self, request: Request) -> Response:
        page = paginate(request.query_params, FeedEvent.objects.all(), FEED_LIST)
        return Response(
            FeedPageSerializer({"events": page.rows, "next_cursor": page.next_cursor}).data
        )


@csrf_exempt
def whatsapp_webhook(request: HttpRequest) -> HttpResponse:
    """Meta webhook. GET: subscription check. POST: status updates, signature-checked, handed to
    Celery; answers 200 at once. Never routed unless WhatsApp is configured."""
    if request.method == "GET":
        token = settings.WHATSAPP_VERIFY_TOKEN
        if (
            token
            and request.GET.get("hub.mode") == "subscribe"
            and request.GET.get("hub.verify_token") == token
        ):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponse(status=403)
    if request.method != "POST":
        return HttpResponse(status=405)
    raw = request.body
    if not signature_valid(raw, request.headers.get("X-Hub-Signature-256")):
        return HttpResponse(status=401)
    try:
        payload = json.loads(raw)
    except ValueError:
        return HttpResponse(status=400)
    from apps.notifications.tasks import process_whatsapp_statuses

    process_whatsapp_statuses.delay(payload)
    return HttpResponse(status=200)
