"""EcoFlow Ocean API exceptions."""

from __future__ import annotations


class EcoflowOceanError(Exception):
    """Base error for pyecoflowocean."""


class InvalidCredentialsError(EcoflowOceanError):
    """Email/password rejected by EcoFlow cloud."""


class AuthenticationError(EcoflowOceanError):
    """Login or token refresh failed."""


class ApiNotMappedError(EcoflowOceanError):
    """API endpoints or payload fields are not yet mapped from app capture."""

    def __init__(self, message: str | None = None) -> None:
        default = (
            "EcoFlow app API is not mapped yet. Capture mobile app traffic, "
            "update docs/api-notes.md, then implement auth/client/parser."
        )
        super().__init__(message or default)
