"""Config flow for EcoFlow Power Ocean."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from .pyecoflowocean import ApiNotMappedError, EcoflowOcean, EcoflowDevice, InvalidCredentialsError
from .pyecoflowocean.client import filter_power_ocean_devices

from .const import CONF_REGION, CONF_SERIAL_NUMBER, DEFAULT_REGION, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _discover_devices(
    username: str,
    password: str,
    region: str = DEFAULT_REGION,
) -> list[EcoflowDevice]:
    """Log in and return available Power Ocean devices."""
    api = EcoflowOcean(username, password, region=region)
    try:
        await api.login()
        devices = await api.get_devices()
    except InvalidCredentialsError as err:
        raise InvalidAuth from err
    except ApiNotMappedError as err:
        raise ApiNotMapped from err
    except Exception as err:
        _LOGGER.exception("Unexpected error during EcoFlow login")
        raise CannotConnect from err
    finally:
        await api.close()

    devices = filter_power_ocean_devices(devices)
    if not devices:
        raise NoDevices
    return devices


class EcoflowOceanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EcoFlow Power Ocean."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._username: str | None = None
        self._password: str | None = None
        self._devices: list[EcoflowDevice] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            try:
                self._devices = await _discover_devices(
                    self._username,
                    self._password,
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except ApiNotMapped:
                errors["base"] = "api_not_mapped"
            except NoDevices:
                errors["base"] = "no_devices"
            else:
                if len(self._devices) == 1:
                    return await self._create_entry(self._devices[0])
                return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user pick an inverter when multiple exist."""
        if user_input is not None:
            serial = user_input[CONF_SERIAL_NUMBER]
            device = next(
                (d for d in self._devices if d.serial_number == serial),
                None,
            )
            if device is None:
                return self.async_show_form(
                    step_id="device",
                    data_schema=self._device_schema(),
                    errors={"base": "device_not_found"},
                )
            return await self._create_entry(device)

        return self.async_show_form(
            step_id="device",
            data_schema=self._device_schema(),
        )

    def _device_schema(self) -> vol.Schema:
        """Schema for device selection."""
        return vol.Schema(
            {
                vol.Required(CONF_SERIAL_NUMBER): vol.In(
                    {
                        d.serial_number: f"{d.name} ({d.serial_number})"
                        for d in self._devices
                    }
                )
            }
        )

    async def _create_entry(self, device: EcoflowDevice) -> config_entries.ConfigFlowResult:
        """Create the config entry."""
        await self.async_set_unique_id(device.serial_number)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=device.name,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_SERIAL_NUMBER: device.serial_number,
                CONF_REGION: DEFAULT_REGION,
            },
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid credentials."""


class ApiNotMapped(HomeAssistantError):
    """Error to indicate API endpoints are not mapped yet."""


class NoDevices(HomeAssistantError):
    """Error to indicate no Power Ocean devices were found."""
