"""EcoFlow Power Ocean Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from .pyecoflowocean import EcoflowOcean, InvalidCredentialsError

from .const import CONF_PRODUCT_TYPE, CONF_SERIAL_NUMBER, DOMAIN
from .coordinator import EcoflowOceanCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EcoFlow Power Ocean from a config entry."""
    api = EcoflowOcean(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        region=entry.data.get("region", "us"),
        serial_number=entry.data[CONF_SERIAL_NUMBER],
        product_type=entry.data.get(CONF_PRODUCT_TYPE, "83"),
    )
    try:
        await api.login()
    except InvalidCredentialsError as err:
        _LOGGER.error("Invalid EcoFlow credentials")
        raise ConfigEntryNotReady("Invalid credentials") from err
    except Exception as err:
        _LOGGER.error("Could not log in to EcoFlow: %s", err)
        raise ConfigEntryNotReady("Cannot connect to EcoFlow") from err

    coordinator = EcoflowOceanCoordinator(hass, entry, api)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        await api.close()
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: EcoflowOceanCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.api.close()
    return unload_ok
