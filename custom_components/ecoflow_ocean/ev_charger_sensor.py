"""Sensor platform for EcoFlow Ocean EV Charger (PowerPulse)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL_EV_CHARGER
from .coordinator import EcoflowOceanCoordinator
from .pyecoflowocean import EcoflowEvChargerState


@dataclass(frozen=True, slots=True)
class EvChargerSensorDefinition:
    """Definition for one EV charger sensor entity."""

    key: str
    name: str
    value_fn: Callable[[EcoflowEvChargerState], float | None]
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None


EV_CHARGER_SENSOR_DEFINITIONS: tuple[EvChargerSensorDefinition, ...] = (
    EvChargerSensorDefinition(
        key="charge_power",
        name="Charge power",
        value_fn=lambda s: s.charge_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    EvChargerSensorDefinition(
        key="output_voltage",
        name="Output voltage",
        value_fn=lambda s: s.output_voltage_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    EvChargerSensorDefinition(
        key="max_current",
        name="Max current",
        value_fn=lambda s: s.max_current_a,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    ),
    EvChargerSensorDefinition(
        key="max_power",
        name="Max power",
        value_fn=lambda s: s.max_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    EvChargerSensorDefinition(
        key="charge_limit",
        name="Charge limit",
        value_fn=lambda s: s.charge_limit_percent,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
)


async def async_setup_ev_charger_sensors(
    coordinator: EcoflowOceanCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV charger sensors."""
    async_add_entities(
        EcoflowEvChargerSensor(coordinator, definition)
        for definition in EV_CHARGER_SENSOR_DEFINITIONS
    )


class EcoflowEvChargerSensor(CoordinatorEntity[EcoflowOceanCoordinator], SensorEntity):
    """EcoFlow Ocean EV Charger sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoflowOceanCoordinator,
        definition: EvChargerSensorDefinition,
    ) -> None:
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
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL_EV_CHARGER,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def native_value(self) -> float | None:
        state = self._ev_state()
        if state is None:
            return None
        return self._definition.value_fn(state)

    @property
    def available(self) -> bool:
        state = self._ev_state()
        if not super().available or state is None:
            return False
        return self._definition.value_fn(state) is not None

    def _ev_state(self) -> EcoflowEvChargerState | None:
        data = self.coordinator.data
        return data if isinstance(data, EcoflowEvChargerState) else None
