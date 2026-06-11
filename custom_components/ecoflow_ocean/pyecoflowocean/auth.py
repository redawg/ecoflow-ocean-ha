"""EcoFlow app authentication (mobile IOT_APP protocol)."""

from __future__ import annotations

import base64
import logging
from typing import Any

import aiohttp

from .const import (
    AUTH_HOST,
    AUTH_LOGIN_PATH,
    DEFAULT_HEADERS,
    DEFAULT_TIMEOUT,
    LOGIN_HEADERS,
    MQTT_CERT_PATH,
)
from .exceptions import AuthenticationError, EcoflowOceanError, InvalidCredentialsError

_LOGGER = logging.getLogger(__name__)


class EcoflowAuth:
    """Manage EcoFlow cloud login, bearer token, and MQTT credentials."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._user_id: str | None = None
        self._user_name: str | None = None
        self._mqtt_cert: dict[str, Any] | None = None

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def user_name(self) -> str | None:
        return self._user_name

    @property
    def mqtt_cert(self) -> dict[str, Any] | None:
        return self._mqtt_cert

    def auth_headers(self) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def login(self) -> None:
        """Authenticate with the EcoFlow mobile app login endpoint."""
        url = f"https://{AUTH_HOST}{AUTH_LOGIN_PATH}"
        payload = {
            "email": self._email,
            "password": base64.b64encode(self._password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        try:
            async with self._session.post(
                url,
                json=payload,
                headers=LOGIN_HEADERS,
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise AuthenticationError(f"Login request failed: {err}") from err

        if resp.status in {401, 403}:
            raise InvalidCredentialsError(f"Invalid EcoFlow credentials ({resp.status})")
        if resp.status >= 400:
            raise AuthenticationError(f"Login failed ({resp.status}): {body}")

        if not isinstance(body, dict):
            raise AuthenticationError("Unexpected login response format")

        data = body.get("data")
        if not isinstance(data, dict):
            raise AuthenticationError(f"Login response missing data: {body}")

        token = data.get("token")
        user = data.get("user")
        if not token or not isinstance(user, dict):
            raise AuthenticationError(f"Login response missing token or user: {body}")

        user_id = user.get("userId")
        if not user_id:
            raise AuthenticationError(f"Login response missing userId: {body}")

        self._token = str(token)
        self._user_id = str(user_id)
        self._user_name = str(user.get("name") or self._email)
        _LOGGER.debug("Logged in to EcoFlow as %s", self._user_name)

        await self._fetch_mqtt_cert()

    async def _fetch_mqtt_cert(self) -> None:
        """Fetch MQTT broker credentials used by the mobile app."""
        if not self._user_id or not self._token:
            raise AuthenticationError("Cannot fetch MQTT cert before login")

        url = f"https://{AUTH_HOST}{MQTT_CERT_PATH}"
        try:
            async with self._session.get(
                url,
                params={"userId": self._user_id},
                headers=self.auth_headers(),
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise EcoflowOceanError(f"MQTT cert request failed: {err}") from err

        if resp.status >= 400:
            _LOGGER.warning("MQTT cert request failed (%s): %s", resp.status, body)
            return

        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            self._mqtt_cert = data

    async def ensure_token(self) -> str:
        if not self._token:
            await self.login()
        if not self._token:
            raise AuthenticationError("No token available after login")
        return self._token
