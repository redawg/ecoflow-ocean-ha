"""Data update coordinator for EcoFlow Power Ocean."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .pyecoflowocean import ApiNotMappedError, EcoflowOcean, EcoflowEvChargerState, EcoflowOceanState, EcoflowPanelState

from .const import CONF_PRODUCT_TYPE, CONF_SERIAL_NUMBER, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EcoflowOceanCoordinator(DataUpdateCoordinator[EcoflowOceanState | EcoflowPanelState | EcoflowEvChargerState]):
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
        self.product_type = entry.data.get(CONF_PRODUCT_TYPE, "83")
        self._mqtt_started = False
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=dt_util.timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=entry,
        )

    async def async_start_mqtt(self) -> None:
        """Start EcoFlow cloud MQTT for live telemetry pushes."""
        if self._mqtt_started:
            return

        loop = asyncio.get_running_loop()

        async def _on_mqtt_update() -> None:
            state = self.api._mqtt.get_state() if self.api._mqtt else None  # noqa: SLF001
            if state is not None:
                self.async_set_updated_data(state)

        try:
            await self.api.start_mqtt(loop, on_update=_on_mqtt_update)
            self._mqtt_started = True
            _LOGGER.info("EcoFlow MQTT listener started for %s", self.serial_number)
        except Exception as err:
            _LOGGER.warning("EcoFlow MQTT unavailable, using REST only: %s", err)

    async def _async_update_data(self) -> EcoflowOceanState | EcoflowPanelState | EcoflowEvChargerState:
        """Refresh device telemetry."""
        if not self._mqtt_started:
            await self.async_start_mqtt()

        try:
            return await self.api.get_system_state(
                self.serial_number,
                product_type=self.product_type,
            )
        except ApiNotMappedError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with EcoFlow API: {err}") from err

    @property
    def device_name(self) -> str:
        """Return a friendly device name."""
        title = self.config_entry.title
        return title if title else f"Power Ocean {self.serial_number}"
