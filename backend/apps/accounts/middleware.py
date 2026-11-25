"""Authentication middleware. Internal to identity; referenced from settings.

Reads ``Authorization: Bearer <access token>``, verifies it through ``apps.accounts.sessions``
(the only module that touches the session tables), and sets the request's identity attributes,
the only source of identity for the rest of the request:

- ``authenticated_user_id``, ``authenticated_chama_id`` (the session's active chama, or
  None), ``authenticated_session``.

On every request the session's active chama is kept only while the user still holds an
ACTIVE membership in it (``apps.organizations.services.checked_identity``, read before any chama
context exists). Otherwise the session's active chama is cleared and the request continues
without one; endpoints that need a chama then answer ``chama_not_available``.

The rest of the request then runs inside ``user_context`` for the verified user (trusted caller,
); ``ChamaContextMiddleware``, which must come after this one (``core.checks``), adds
the chama context. A bad token does not raise here: the error is kept on the request and
``BearerSessionAuthentication`` turns it into a 401 for API views. Nothing is ever read from the
body, query string or other headers.
"""

from collections.abc import Callable

from django.db import transaction
from django.http import HttpRequest, HttpResponse

from apps.accounts.authentication import AUTH_ERROR_ATTR, SESSION_ATTR
from apps.accounts.sessions import authenticate_access
from apps.organizations.services import checked_identity
from core.api_errors import ApiError
from core.tenancy import AUTHENTICATED_CHAMA_ATTR, AUTHENTICATED_USER_ATTR, user_context

_BEARER = "bearer "


class SessionAuthenticationMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        setattr(request, AUTHENTICATED_USER_ATTR, None)
        setattr(request, AUTHENTICATED_CHAMA_ATTR, None)
        setattr(request, SESSION_ATTR, None)
        setattr(request, AUTH_ERROR_ATTR, None)
        header = request.headers.get("Authorization", "")
        if not header.lower().startswith(_BEARER):
            return self.get_response(request)
        try:
            identity = authenticate_access(header[len(_BEARER) :].strip())
        except ApiError as error:
            setattr(request, AUTH_ERROR_ATTR, error)
            return self.get_response(request)
        identity = checked_identity(identity)
        setattr(request, AUTHENTICATED_USER_ATTR, identity.user_id)
        setattr(request, AUTHENTICATED_CHAMA_ATTR, identity.active_chama_id)
        setattr(request, SESSION_ATTR, identity)
        with user_context(identity.user_id):  # trusted caller: the token was just verified
            response = self.get_response(request)
            if response.status_code >= 500:
                transaction.set_rollback(True)
            return response
