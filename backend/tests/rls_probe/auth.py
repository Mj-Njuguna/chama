"""Fake session authentication for tests: ``Authorization: Test <token>`` maps to a chama via
SESSIONS and to a user via USERS, standing in for the real session lookup.
Only this middleware sets the authenticated chama and user; nothing else in the request can."""

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from core.tenancy import AUTHENTICATED_CHAMA_ATTR, AUTHENTICATED_USER_ATTR

SESSIONS: dict[str, uuid.UUID] = {}  # token -> active chama
USERS: dict[str, uuid.UUID] = {}  # token -> user
PATH = "tests.rls_probe.auth.FakeSessionAuthenticationMiddleware"


class FakeSessionAuthenticationMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        valid = scheme == "Test"
        setattr(request, AUTHENTICATED_CHAMA_ATTR, SESSIONS.get(token) if valid else None)
        setattr(request, AUTHENTICATED_USER_ATTR, USERS.get(token) if valid else None)
        return self.get_response(request)
