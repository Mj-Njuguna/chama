"""Errors shared inside the organizations module."""

from rest_framework import status

from core.api_errors import ApiError


def chama_not_available() -> ApiError:
    """One answer for an unknown chama, no membership, or a membership that is not active."""
    return ApiError(
        "chama_not_available",
        "This chama is not available.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def member_not_found() -> ApiError:
    return ApiError("not_found", "No such member.", status_code=status.HTTP_404_NOT_FOUND)
