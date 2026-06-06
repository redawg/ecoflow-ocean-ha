"""Unofficial EcoFlow Power Ocean API client (US app protocol)."""

from pyecoflowocean.client import EcoflowOcean
from pyecoflowocean.exceptions import (
    ApiNotMappedError,
    AuthenticationError,
    EcoflowOceanError,
    InvalidCredentialsError,
)
from pyecoflowocean.models import EcoflowDevice, EcoflowOceanState

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
