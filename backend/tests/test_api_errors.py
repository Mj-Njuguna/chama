"""One error shape for every API error (core.api_errors)."""

from typing import Any

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, serializers
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.api_errors import ApiError


class _Body(serializers.Serializer[Any]):
    phone = serializers.CharField()


class _Bearer(BaseAuthentication):
    """Authenticates nobody; present so DRF answers 401 (with WWW-Authenticate), not 403."""

    def authenticate(self, request: Request) -> None:
        return None

    def authenticate_header(self, request: Request) -> str:
        return "Bearer"


def _call(exc: Exception | None = None, data: dict[str, Any] | None = None) -> Response:
    class View(APIView):
        authentication_classes = (_Bearer,)
        permission_classes = (AllowAny,)

        def post(self, request: Request) -> Response:
            if exc is not None:
                raise exc
            _Body(data=request.data).is_valid(raise_exception=True)
            return Response({"ok": True})

    request = APIRequestFactory().post("/x", data or {}, format="json")
    response = View.as_view()(request)
    response.render()
    return response


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (ApiError("invalid_code", "The code is not valid."), 400, "invalid_code"),
        (ApiError("locked", "Try later.", status_code=429, retry_after=900), 429, "locked"),
        (exceptions.NotAuthenticated(), 401, "not_authenticated"),
        (exceptions.PermissionDenied(), 403, "permission_denied"),
        (DjangoPermissionDenied("no"), 403, "permission_denied"),
        (Http404(), 404, "not_found"),
        (exceptions.Throttled(wait=30), 429, "rate_limited"),
        (exceptions.ParseError(), 400, "malformed_request"),
    ],
)
def test_every_error_has_the_same_shape(exc: Exception, status: int, code: str):
    response = _call(exc)
    assert response.status_code == status
    body = response.data
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_extra_fields_are_included():
    error = _call(ApiError("locked", "Try later.", status_code=429, retry_after=900)).data["error"]
    assert error["retry_after"] == 900
    assert _call(exceptions.Throttled(wait=30)).data["error"]["retry_after"] == 30


def test_validation_errors_list_the_fields():
    error = _call(data={}).data["error"]
    assert error["code"] == "validation_error"
    assert set(error["field_errors"]) == {"phone"}


def test_unhandled_exceptions_are_not_swallowed():
    with pytest.raises(RuntimeError):
        _call(RuntimeError("boom"))
