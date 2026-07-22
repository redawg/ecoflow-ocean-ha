"""Binary sensor platform for EcoFlow Power Ocean ecosystem devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL_EV_CHARGER, MODEL_OCEAN_PANEL, MODEL_POWER_OCEAN
from .coordinator import EcoflowOceanCoordinator
from .pyecoflowocean import EcoflowEvChargerState, EcoflowPanelState
from .pyecoflowocean.const import PRODUCT_TYPE_EV_CHARGER, PRODUCT_TYPE_OCEAN_PANEL


@dataclass(frozen=True, slots=True)
class BinarySensorDefinition:
    """Definition for one EcoFlow binary sensor entity."""

    key: str
    name: str
    value_fn: Callable[[object], bool | None]
    device_class: BinarySensorDeviceClass | None = None


ONLINE_DEFINITION = BinarySensorDefinition(
    key="online",
    name="Online",
    value_fn=lambda state: getattr(state, "online", None),
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)

PANEL_DEFINITIONS: tuple[BinarySensorDefinition, ...] = (
    ONLINE_DEFINITION,
    BinarySensorDefinition(
        key="storm_watch",
        name="Storm Watch",
        value_fn=lambda state: getattr(state, "storm_watch", None)
        if isinstance(state, EcoflowPanelState)
        else None,
    ),
    BinarySensorDefinition(
        key="storm_enabled",
        name="Storm mode active",
        value_fn=lambda state: getattr(state, "storm_enabled", None)
        if isinstance(state, EcoflowPanelState)
        else None,
    ),
)

EV_DEFINITIONS: tuple[BinarySensorDefinition, ...] = (
    ONLINE_DEFINITION,
    BinarySensorDefinition(
        key="vehicle_connected",
        name="Vehicle connected",
        value_fn=lambda state: getattr(state, "vehicle_connected", None)
        if isinstance(state, EcoflowEvChargerState)
        else None,
        device_class=BinarySensorDeviceClass.PLUG,
    ),
    BinarySensorDefinition(
        key="charging_active",
        name="Charging active",
        value_fn=lambda state: getattr(state, "charging_active", None)
        if isinstance(state, EcoflowEvChargerState)
        else None,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoFlow binary sensors."""
    coordinator: EcoflowOceanCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.product_type == PRODUCT_TYPE_OCEAN_PANEL:
        definitions = PANEL_DEFINITIONS
    elif coordinator.product_type == PRODUCT_TYPE_EV_CHARGER:
        definitions = EV_DEFINITIONS
    else:
        definitions = (ONLINE_DEFINITION,)

    async_add_entities(
        EcoflowOceanBinarySensor(coordinator, definition) for definition in definitions
    )


class EcoflowOceanBinarySensor(
    CoordinatorEntity[EcoflowOceanCoordinator], BinarySensorEntity
):
    """EcoFlow binary sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoflowOceanCoordinator,
        definition: BinarySensorDefinition,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._definition = definition
        sn = coordinator.serial_number
        self._attr_unique_id = f"{sn}_{definition.key}"
        self._attr_name = definition.name
        self._attr_device_class = definition.device_class

    @property
    def device_info(self) -> DeviceInfo:
        product_type = self.coordinator.product_type
        if product_type == PRODUCT_TYPE_OCEAN_PANEL:
            model = MODEL_OCEAN_PANEL
        elif product_type == PRODUCT_TYPE_EV_CHARGER:
            model = MODEL_EV_CHARGER
        else:
            model = MODEL_POWER_OCEAN
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=model,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def is_on(self) -> bool | None:
        """Return binary sensor state."""
        if not self.coordinator.data:
            return None
        return self._definition.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True when coordinator has usable data."""
        if not super().available or not self.coordinator.data:
            return False
        return self._definition.value_fn(self.coordinator.data) is not None
