"""DRF authentication backed by the session middleware.

DRF authenticates lazily inside the view, too late to set the chama context, so the real work
happens in ``SessionAuthenticationMiddleware``. This class only exposes its result to DRF: the
verified identity, or the token error as a 401 with a stable code.
"""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

if TYPE_CHECKING:  # DRF imports this module while its own views module is loading
    from apps.accounts.sessions import SessionIdentity

# Request attributes set by SessionAuthenticationMiddleware.
SESSION_ATTR = "authenticated_session"
AUTH_ERROR_ATTR = "authentication_error"


@dataclass(frozen=True)
class AuthenticatedUser:
    """``request.user`` for API views: who, never a model instance."""

    id: uuid.UUID
    is_authenticated: bool = True


class BearerSessionAuthentication(BaseAuthentication):
    def authenticate(self, request: Request) -> "tuple[AuthenticatedUser, SessionIdentity] | None":
        raw = request._request
        error = getattr(raw, AUTH_ERROR_ATTR, None)
        if error is not None:
            raise error
        identity: SessionIdentity | None = getattr(raw, SESSION_ATTR, None)
        if identity is None:
            return None
        return AuthenticatedUser(identity.user_id), identity

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'
