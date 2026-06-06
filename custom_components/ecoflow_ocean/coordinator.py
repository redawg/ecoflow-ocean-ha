"""Data update coordinator for EcoFlow Power Ocean."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pyecoflowocean import ApiNotMappedError, EcoflowOcean, EcoflowOceanState

from .const import CONF_SERIAL_NUMBER, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EcoflowOceanCoordinator(DataUpdateCoordinator[EcoflowOceanState]):
    """Fetch and cache Power Ocean telemetry."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: EcoflowOcean,
    ) -> None:
        """Initialize."""
        self.api = api
        self.serial_number = entry.data[CONF_SERIAL_NUMBER]
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=dt_util.timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=entry,
        )

    async def _async_update_data(self) -> EcoflowOceanState:
        """Refresh Power Ocean telemetry."""
        try:
            return await self.api.get_system_state(self.serial_number)
        except ApiNotMappedError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with EcoFlow API: {err}") from err

    @property
    def device_name(self) -> str:
        """Return a friendly device name."""
        title = self.config_entry.title
        return title if title else f"Power Ocean {self.serial_number}"
