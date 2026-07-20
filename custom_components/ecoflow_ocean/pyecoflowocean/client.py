"""Async EcoFlow Power Ocean API client (mobile app protocol)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .auth import EcoflowAuth
from .const import (
    AUTH_HOST,
    DEFAULT_REGION,
    DEFAULT_TIMEOUT,
    DEVICE_DETAIL_PATH,
    DEVICE_LIST_PATH,
    OCEAN_ECOSYSTEM_PRODUCT_TYPES,
    POWER_OCEAN_PRODUCT_TYPES,
    PRODUCT_TYPE_NAMES,
    PRODUCT_TYPE_POWER_OCEAN,
    REGION_HOSTS,
)
from .exceptions import ApiNotMappedError, AuthenticationError, EcoflowOceanError
from .const import PRODUCT_TYPE_OCEAN_PANEL, PRODUCT_TYPE_EV_CHARGER
from .models import EcoflowDevice, EcoflowEvChargerState, EcoflowOceanState, EcoflowPanelState
from .mqtt import EcoflowMqttListener
from .parser import parse_device, parse_system_state

_LOGGER = logging.getLogger(__name__)


class EcoflowOcean:
    """Unofficial client for EcoFlow Power Ocean via the mobile app API."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        region: str = DEFAULT_REGION,
        serial_number: str | None = None,
        product_type: str = PRODUCT_TYPE_POWER_OCEAN,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._region = region.lower()
        self._serial_number = serial_number
        self._product_type = product_type
        self._session = session
        self._owns_session = session is None
        self._auth: EcoflowAuth | None = None
        self._api_host: str | None = None
        self._mqtt: EcoflowMqttListener | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def api_host(self) -> str | None:
        return self._api_host

    @property
    def product_type(self) -> str:
        return self._product_type

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get_auth(self) -> EcoflowAuth:
        if self._auth is None:
            session = await self._get_session()
            self._auth = EcoflowAuth(session, email=self._email, password=self._password)
        return self._auth

    async def login(self) -> None:
        """Log in to EcoFlow cloud and detect the regional API host."""
        auth = await self._get_auth()
        await auth.login()
        if self._serial_number:
            await self._detect_region(self._serial_number, self._product_type)

    async def _detect_region(self, serial_number: str, product_type: str) -> None:
        auth = await self._get_auth()
        await auth.ensure_token()

        preferred = REGION_HOSTS.get(self._region)
        hosts = []
        if preferred:
            hosts.append(preferred)
        hosts.extend(host for host in REGION_HOSTS.values() if host not in hosts)

        session = await self._get_session()
        for host in hosts:
            url = f"https://{host}{DEVICE_DETAIL_PATH}"
            headers = {
                **auth.auth_headers(),
                "product-type": product_type,
            }
            try:
                async with session.get(
                    url,
                    params={"sn": serial_number},
                    headers=headers,
                    timeout=DEFAULT_TIMEOUT,
                ) as resp:
                    if resp.status == 200:
                        self._api_host = host
                        _LOGGER.debug("Detected EcoFlow API region host: %s", host)
                        return
            except aiohttp.ClientError as err:
                _LOGGER.debug("Region probe failed for %s: %s", host, err)

        raise EcoflowOceanError(
            "Could not detect EcoFlow API region. Verify serial number and product type."
        )

    async def get_devices(self) -> list[EcoflowDevice]:
        """Return Power Ocean devices visible to the account."""
        auth = await self._get_auth()
        await auth.ensure_token()
        session = await self._get_session()

        url = f"https://{AUTH_HOST}{DEVICE_LIST_PATH}"
        try:
            async with session.get(
                url,
                headers=auth.auth_headers(),
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    raise ApiNotMappedError(f"Device list failed ({resp.status})")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise EcoflowOceanError(f"Device list request failed: {err}") from err

        devices: list[EcoflowDevice] = []
        data = body.get("data") if isinstance(body, dict) else body
        if isinstance(data, dict):
            bound = data.get("bound")
            if isinstance(bound, dict):
                for serial, raw in bound.items():
                    if isinstance(raw, dict):
                        entry = {"sn": serial, **raw}
                        device = parse_device(entry)
                        if device is not None:
                            devices.append(device)
        elif isinstance(data, list):
            for raw in data:
                if isinstance(raw, dict):
                    device = parse_device(raw)
                    if device is not None:
                        devices.append(device)

        devices = filter_power_ocean_devices(devices)
        if devices:
            return devices

        if self._serial_number:
            name = PRODUCT_TYPE_NAMES.get(self._product_type, "Power Ocean")
            return [
                EcoflowDevice(
                    serial_number=self._serial_number,
                    name=name,
                    product_type=self._product_type,
                )
            ]

        raise ApiNotMappedError(
            "No devices returned from the mobile app list API. "
            "Enter your inverter serial number during setup."
        )

    async def get_raw_telemetry(
        self,
        serial_number: str,
        *,
        product_type: str | None = None,
    ) -> dict[str, Any]:
        """Return raw provider-service JSON."""
        auth = await self._get_auth()
        await auth.ensure_token()

        product_type = product_type or self._product_type
        if not self._api_host:
            await self._detect_region(serial_number, product_type)

        assert self._api_host is not None
        session = await self._get_session()
        url = f"https://{self._api_host}{DEVICE_DETAIL_PATH}"
        headers = {
            **auth.auth_headers(),
            "product-type": product_type,
        }

        try:
            async with session.get(
                url,
                params={"sn": serial_number},
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise EcoflowOceanError(f"Telemetry request failed: {err}") from err

        if resp.status >= 400:
            raise EcoflowOceanError(f"Telemetry request failed ({resp.status}): {body}")

        if not isinstance(body, dict):
            raise EcoflowOceanError("Unexpected telemetry response format")
        return body

    async def get_system_state(
        self,
        serial_number: str,
        *,
        product_type: str | None = None,
    ) -> EcoflowOceanState | EcoflowPanelState | EcoflowEvChargerState:
        """Return parsed live telemetry for one device serial number."""
        product_type = product_type or self._product_type
        if product_type == PRODUCT_TYPE_OCEAN_PANEL:
            return await self.get_panel_state(serial_number)
        if product_type == PRODUCT_TYPE_EV_CHARGER:
            return await self.get_ev_charger_state(serial_number)

        mqtt_state = self._mqtt.get_inverter_state() if self._mqtt else None
        if mqtt_state is not None and any(
            value is not None
            for value in (
                mqtt_state.battery_soc,
                mqtt_state.home_power_w,
                mqtt_state.grid_power_w,
                mqtt_state.solar_power_w,
                mqtt_state.battery_power_w,
            )
        ):
            mqtt_state.online = True
            return mqtt_state

        raw = await self.get_raw_telemetry(
            serial_number,
            product_type=product_type,
        )
        rest_state = parse_system_state(serial_number, raw)
        if mqtt_state is not None:
            # Prefer MQTT values when REST returns empty quota stubs.
            for field in (
                "battery_soc",
                "battery_power_w",
                "solar_power_w",
                "grid_power_w",
                "home_power_w",
                "work_mode",
                "status",
                "backup_soc_limit",
                "discharge_soc_limit",
                "feed_power_limit_w",
                "feed_ratio_percent",
                "phase_a_voltage_v",
                "phase_a_current_a",
                "phase_a_power_w",
                "phase_b_voltage_v",
                "phase_b_current_a",
                "phase_b_power_w",
                "phase_c_voltage_v",
                "phase_c_current_a",
                "phase_c_power_w",
            ):
                mqtt_val = getattr(mqtt_state, field)
                rest_val = getattr(rest_state, field)
                if mqtt_val is not None and (rest_val is None or rest_val == 0):
                    setattr(rest_state, field, mqtt_val)
        return rest_state

    async def get_panel_state(self, serial_number: str) -> EcoflowPanelState:
        """Return parsed Ocean Panel telemetry."""
        from datetime import datetime, timezone

        mqtt_state = self._mqtt.get_panel_state() if self._mqtt else None
        if mqtt_state is not None and (
            mqtt_state.circuit_power_w or mqtt_state.grid_voltage_v is not None
        ):
            mqtt_state.online = True
            return mqtt_state

        raw = await self.get_raw_telemetry(serial_number, product_type=PRODUCT_TYPE_OCEAN_PANEL)
        data = raw.get("data") if isinstance(raw, dict) else {}
        online = bool(data.get("online") == 1) if isinstance(data, dict) else None

        state = EcoflowPanelState(
            serial_number=serial_number,
            online=online,
            updated_at=datetime.now(tz=timezone.utc),
            raw={"response": raw},
        )
        if mqtt_state is not None:
            for field in (
                "grid_voltage_v",
                "grid_voltage_l1_v",
                "grid_voltage_l2_v",
                "home_power_w",
                "inverter_feed_power_w",
                "grid_import_power_w",
                "master_grid_power_w",
                "channel_sum_power_w",
                "hall_total_power_w",
                "backup_reserve_soc",
                "solar_backup_reserve_soc",
                "storm_enabled",
                "linked_ev_charger_serial",
                "ev_charge_power_w",
                "circuit_power_w",
                "circuit_voltage_v",
                "circuit_active",
                "circuit_names",
                "circuit_set_amp",
            ):
                mqtt_val = getattr(mqtt_state, field)
                if mqtt_val is not None and (
                    not isinstance(mqtt_val, (dict, list)) or mqtt_val
                ):
                    setattr(state, field, mqtt_val)
            if state.circuit_power_w and state.home_power_w is None:
                from .panel_decoder import _branch_home_power_w

                state.home_power_w = _branch_home_power_w(state.circuit_power_w)
            if state.circuit_power_w and state.inverter_feed_power_w is None:
                from .panel_decoder import _inverter_feed_power_w

                state.inverter_feed_power_w = _inverter_feed_power_w(state.circuit_power_w)
        return state

    async def get_ev_charger_state(self, serial_number: str) -> EcoflowEvChargerState:
        """Return parsed Ocean EV Charger telemetry."""
        from datetime import datetime, timezone

        mqtt_state = self._mqtt.get_ev_charger_state() if self._mqtt else None
        if mqtt_state is not None and any(
            value is not None
            for value in (
                mqtt_state.charge_power_w,
                mqtt_state.vehicle_connected,
                mqtt_state.charging_active,
                mqtt_state.output_voltage_v,
            )
        ):
            mqtt_state.online = True
            return mqtt_state

        raw = await self.get_raw_telemetry(serial_number, product_type=PRODUCT_TYPE_EV_CHARGER)
        data = raw.get("data") if isinstance(raw, dict) else {}
        online = bool(data.get("online") == 1) if isinstance(data, dict) else None

        state = EcoflowEvChargerState(
            serial_number=serial_number,
            online=online,
            updated_at=datetime.now(tz=timezone.utc),
            raw={"response": raw},
        )
        if mqtt_state is not None:
            for field in (
                "charge_power_w",
                "output_voltage_v",
                "max_current_a",
                "max_power_w",
                "charge_limit_percent",
                "vehicle_connected",
                "charging_active",
            ):
                mqtt_val = getattr(mqtt_state, field)
                if mqtt_val is not None:
                    setattr(state, field, mqtt_val)
        return state

    async def start_mqtt(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        on_update: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Start cloud MQTT listener for live telemetry."""
        if self._mqtt is not None:
            return
        if not self._serial_number:
            raise EcoflowOceanError("Serial number required for MQTT listener")

        auth = await self._get_auth()
        await auth.ensure_token()
        if not auth.mqtt_cert or not auth.user_id:
            raise EcoflowOceanError("MQTT credentials unavailable after login")

        self._loop = loop
        self._mqtt = EcoflowMqttListener(
            auth,
            self._serial_number,
            product_type=self._product_type,
            loop=loop,
            on_update=on_update,
        )
        await self._mqtt.start()

    async def stop_mqtt(self) -> None:
        """Stop cloud MQTT listener."""
        if self._mqtt is not None:
            await self._mqtt.stop()
            self._mqtt = None

    async def close(self) -> None:
        await self.stop_mqtt()
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> EcoflowOcean:
        await self.login()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def filter_power_ocean_devices(devices: list[EcoflowDevice]) -> list[EcoflowDevice]:
    """Keep likely Power Ocean / CDO Ocean devices from a discovery list."""
    ocean_tokens = ("ocean", "cdo", "hj31", "hj37", "hr51", "hr61", "r37", "hc31")
    filtered = [
        device
        for device in devices
        if device.product_type in POWER_OCEAN_PRODUCT_TYPES
        or device.product_type in OCEAN_ECOSYSTEM_PRODUCT_TYPES
        or any(
            token in f"{device.name} {device.serial_number}".lower()
            for token in ocean_tokens
        )
    ]
    return filtered or devices
