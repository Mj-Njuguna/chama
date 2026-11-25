"""OpenAPI description of the bearer session authentication (drf-spectacular extension)."""

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerSessionScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "apps.accounts.authentication.BearerSessionAuthentication"
    name = "bearerSession"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "description": "Opaque access token (chm_at_...) from /auth/otp/verify or "
            "/auth/token/refresh. Valid 15 minutes.",
        }
