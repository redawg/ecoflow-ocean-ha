"""EcoFlow app authentication — implement after HAR capture."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from pyecoflowocean.const import AUTH_LOGIN_PATH, DEFAULT_HEADERS, DEFAULT_TIMEOUT
from pyecoflowocean.exceptions import ApiNotMappedError, AuthenticationError

_LOGGER = logging.getLogger(__name__)


class EcoflowAuth:
    """Manage EcoFlow cloud login and bearer token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        email: str,
        password: str,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._token: str | None = None
        self._profile: dict[str, Any] | None = None

    @property
    def token(self) -> str | None:
        """Return the current bearer token, if logged in."""
        return self._token

    @property
    def profile(self) -> dict[str, Any] | None:
        """Return the user profile payload from login, if available."""
        return self._profile

    def auth_headers(self) -> dict[str, str]:
        """Return request headers including authorization."""
        headers = dict(DEFAULT_HEADERS)
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def login(self) -> None:
        """Authenticate with EcoFlow cloud using captured login endpoint."""
        raise ApiNotMappedError(
            "Login endpoint not implemented. Capture the EcoFlow mobile app, "
            f"document auth in docs/api-notes.md, then implement "
            f"{self._base_url}{AUTH_LOGIN_PATH}."
        )

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON helper used once endpoints are mapped."""
        url = f"{self._base_url}{path}"
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=DEFAULT_HEADERS,
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise AuthenticationError(f"Login request failed: {err}") from err

        if resp.status >= 400:
            raise AuthenticationError(f"Login failed ({resp.status}): {body}")

        if not isinstance(body, dict):
            raise AuthenticationError("Unexpected login response format")
        return body

    async def ensure_token(self) -> str:
        """Return a valid token, refreshing if needed."""
        if not self._token:
            await self.login()
        if not self._token:
            raise AuthenticationError("No token available after login")
        return self._token
