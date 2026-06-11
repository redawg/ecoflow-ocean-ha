"""Sensor platform for EcoFlow Power Ocean."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .pyecoflowocean import EcoflowOceanState

from .const import DOMAIN, MANUFACTURER, MODEL_POWER_OCEAN
from .coordinator import EcoflowOceanCoordinator


@dataclass(frozen=True, slots=True)
class SensorDefinition:
    """Definition for one Power Ocean sensor entity."""

    key: str
    name: str
    value_fn: Callable[[EcoflowOceanState], float | str | None]
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None


SENSOR_DEFINITIONS: tuple[SensorDefinition, ...] = (
    SensorDefinition(
        key="battery_soc",
        name="Battery SOC",
        value_fn=lambda s: s.battery_soc,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    SensorDefinition(
        key="battery_power",
        name="Battery power",
        value_fn=lambda s: s.battery_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="solar_power",
        name="Solar power",
        value_fn=lambda s: s.solar_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="grid_power",
        name="Grid power",
        value_fn=lambda s: s.grid_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="home_power",
        name="Home power",
        value_fn=lambda s: s.home_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="status",
        name="Status",
        value_fn=lambda s: s.status,
    ),
    SensorDefinition(
        key="work_mode",
        name="Work mode",
        value_fn=lambda s: s.work_mode,
    ),
    SensorDefinition(
        key="backup_soc_limit",
        name="Backup SOC limit",
        value_fn=lambda s: s.backup_soc_limit,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    SensorDefinition(
        key="discharge_soc_limit",
        name="Discharge SOC limit",
        value_fn=lambda s: s.discharge_soc_limit,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    SensorDefinition(
        key="feed_power_limit",
        name="Feed power limit",
        value_fn=lambda s: s.feed_power_limit_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="feed_ratio",
        name="Feed ratio",
        value_fn=lambda s: s.feed_ratio_percent,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    SensorDefinition(
        key="phase_a_voltage",
        name="Phase A voltage",
        value_fn=lambda s: s.phase_a_voltage_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SensorDefinition(
        key="phase_a_current",
        name="Phase A current",
        value_fn=lambda s: s.phase_a_current_a,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    ),
    SensorDefinition(
        key="phase_a_power",
        name="Phase A power",
        value_fn=lambda s: s.phase_a_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="phase_b_voltage",
        name="Phase B voltage",
        value_fn=lambda s: s.phase_b_voltage_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SensorDefinition(
        key="phase_b_current",
        name="Phase B current",
        value_fn=lambda s: s.phase_b_current_a,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    ),
    SensorDefinition(
        key="phase_b_power",
        name="Phase B power",
        value_fn=lambda s: s.phase_b_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    SensorDefinition(
        key="phase_c_voltage",
        name="Phase C voltage",
        value_fn=lambda s: s.phase_c_voltage_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SensorDefinition(
        key="phase_c_current",
        name="Phase C current",
        value_fn=lambda s: s.phase_c_current_a,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    ),
    SensorDefinition(
        key="phase_c_power",
        name="Phase C power",
        value_fn=lambda s: s.phase_c_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoFlow Power Ocean sensors."""
    coordinator: EcoflowOceanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EcoflowOceanSensor(coordinator, definition) for definition in SENSOR_DEFINITIONS
    )


class EcoflowOceanSensor(CoordinatorEntity[EcoflowOceanCoordinator], SensorEntity):
    """EcoFlow Power Ocean sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoflowOceanCoordinator,
        definition: SensorDefinition,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._definition = definition
        sn = coordinator.serial_number
        self._attr_unique_id = f"{sn}_{definition.key}"
        self._attr_name = definition.name
        self._attr_device_class = definition.device_class
        self._attr_state_class = definition.state_class
        self._attr_native_unit_of_measurement = definition.native_unit_of_measurement

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL_POWER_OCEAN,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def native_value(self) -> float | str | None:
        """Return sensor value."""
        if not self.coordinator.data:
            return None
        return self._definition.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True when coordinator has usable data."""
        if not super().available or not self.coordinator.data:
            return False
        value = self._definition.value_fn(self.coordinator.data)
        return value is not None
