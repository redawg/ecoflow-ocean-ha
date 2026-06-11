"""Config flow for EcoFlow Power Ocean."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_PRODUCT_TYPE,
    CONF_REGION,
    CONF_SERIAL_NUMBER,
    DEFAULT_REGION,
    DOMAIN,
    PRODUCT_TYPE_OPTIONS,
)
from .pyecoflowocean import EcoflowOcean, InvalidCredentialsError
from .pyecoflowocean.const import PRODUCT_TYPE_POWER_OCEAN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERIAL_NUMBER): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_PRODUCT_TYPE, default=PRODUCT_TYPE_POWER_OCEAN): vol.In(
            PRODUCT_TYPE_OPTIONS
        ),
    }
)


async def _validate_credentials(
    username: str,
    password: str,
    serial_number: str,
    product_type: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Log in and verify telemetry for the configured inverter."""
    api = EcoflowOcean(
        username,
        password,
        region=region,
        serial_number=serial_number.strip(),
        product_type=product_type,
    )
    try:
        await api.login()
        await api.get_system_state(serial_number.strip(), product_type=product_type)
    except InvalidCredentialsError as err:
        raise InvalidAuth from err
    except Exception as err:
        _LOGGER.exception("EcoFlow setup validation failed")
        raise CannotConnect from err
    finally:
        await api.close()


class EcoflowOceanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EcoFlow Power Ocean."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial_number = user_input[CONF_SERIAL_NUMBER].strip()
            try:
                await _validate_credentials(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    serial_number,
                    user_input[CONF_PRODUCT_TYPE],
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(serial_number)
                self._abort_if_unique_id_configured()
                product_label = PRODUCT_TYPE_OPTIONS[user_input[CONF_PRODUCT_TYPE]]
                return self.async_create_entry(
                    title=f"{product_label} ({serial_number})",
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_SERIAL_NUMBER: serial_number,
                        CONF_PRODUCT_TYPE: user_input[CONF_PRODUCT_TYPE],
                        CONF_REGION: DEFAULT_REGION,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate invalid credentials."""
