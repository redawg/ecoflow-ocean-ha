"""Async EcoFlow Power Ocean API client."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from pyecoflowocean.auth import EcoflowAuth
from pyecoflowocean.const import DEFAULT_REGION, DEFAULT_TIMEOUT, DEVICES_PATH, TELEMETRY_PATH
from pyecoflowocean.exceptions import ApiNotMappedError
from pyecoflowocean.models import EcoflowDevice, EcoflowOceanState

_LOGGER = logging.getLogger(__name__)

REGION_BASE_URLS = {
    "us": "https://api.ecoflow.com",
}


class EcoflowOcean:
    """Unofficial client for EcoFlow Power Ocean (US app API)."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        region: str = DEFAULT_REGION,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._region = region.lower()
        self._session = session
        self._owns_session = session is None
        self._auth: EcoflowAuth | None = None

    @property
    def base_url(self) -> str:
        """Return REST base URL for the configured region."""
        try:
            return REGION_BASE_URLS[self._region]
        except KeyError as err:
            raise ApiNotMappedError(f"Unsupported region: {self._region}") from err

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_auth(self) -> EcoflowAuth:
        if self._auth is None:
            session = await self._get_session()
            self._auth = EcoflowAuth(
                session,
                base_url=self.base_url,
                email=self._email,
                password=self._password,
            )
        return self._auth

    async def login(self) -> None:
        """Log in to EcoFlow cloud."""
        auth = await self._get_auth()
        await auth.login()

    async def get_devices(self) -> list[EcoflowDevice]:
        """Return Power Ocean devices on the account."""
        raise ApiNotMappedError(
            "Device discovery not implemented. Capture the EcoFlow app, document "
            f"the list endpoint in docs/api-notes.md, then implement {DEVICES_PATH}."
        )

    async def get_system_state(self, serial_number: str) -> EcoflowOceanState:
        """Return parsed live telemetry for one inverter serial number."""
        raise ApiNotMappedError(
            "Telemetry fetch not implemented. Capture the EcoFlow app, document "
            f"the polling endpoint in docs/api-notes.md, then implement "
            f"{TELEMETRY_PATH.format(sn=serial_number)}."
        )

    async def get_raw_telemetry(self, serial_number: str) -> dict[str, Any]:
        """Return raw telemetry JSON before parser mapping (for discovery)."""
        auth = await self._get_auth()
        await auth.ensure_token()
        raise ApiNotMappedError(
            "Raw telemetry fetch not implemented — complete API mapping first."
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> EcoflowOcean:
        await self.login()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def filter_power_ocean_devices(devices: list[EcoflowDevice]) -> list[EcoflowDevice]:
    """Keep likely Power Ocean devices from a discovery list."""
    keywords = ("ocean", "power", "re307", "re305", "dpu", "inverter")
    filtered: list[EcoflowDevice] = []
    for device in devices:
        blob = f"{device.product_type} {device.name} {device.serial_number}".lower()
        if any(word in blob for word in keywords):
            filtered.append(device)
    return filtered or devices
