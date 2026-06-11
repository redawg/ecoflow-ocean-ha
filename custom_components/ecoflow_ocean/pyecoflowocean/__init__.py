"""Unofficial EcoFlow Power Ocean API client (US app protocol)."""

from .client import EcoflowOcean
from .exceptions import (
    ApiNotMappedError,
    AuthenticationError,
    EcoflowOceanError,
    InvalidCredentialsError,
)
from .models import EcoflowDevice, EcoflowOceanState

__all__ = [
    "ApiNotMappedError",
    "AuthenticationError",
    "EcoflowDevice",
    "EcoflowOcean",
    "EcoflowOceanError",
    "EcoflowOceanState",
    "InvalidCredentialsError",
]

__version__ = "0.1.0"
