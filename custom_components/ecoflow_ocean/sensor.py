"""Sensor platform for EcoFlow Power Ocean."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
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
    value_fn: Callable[[EcoflowOceanState], float | str | datetime | None]
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
        key="last_updated",
        name="Last updated",
        value_fn=lambda s: s.updated_at,
        device_class=SensorDeviceClass.TIMESTAMP,
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
    SensorDefinition(
        key="battery_pack_count",
        name="Battery pack count",
        value_fn=lambda s: float(len(s.battery_packs)) if s.battery_packs else None,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

MAX_BATTERY_PACKS = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoFlow sensors."""
    coordinator: EcoflowOceanCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.product_type == "95":
        from .panel_sensor import async_setup_panel_sensors

        await async_setup_panel_sensors(coordinator, async_add_entities)
        return
    if coordinator.product_type == "99":
        from .ev_charger_sensor import async_setup_ev_charger_sensors

        await async_setup_ev_charger_sensors(coordinator, async_add_entities)
        return

    entities: list[SensorEntity] = [
        EcoflowOceanSensor(coordinator, definition) for definition in SENSOR_DEFINITIONS
    ]
    for pack_index in range(1, MAX_BATTERY_PACKS + 1):
        entities.append(EcoflowBatteryPackSensor(coordinator, pack_index, "soc"))
        entities.append(EcoflowBatteryPackSensor(coordinator, pack_index, "power"))
        entities.append(EcoflowBatteryPackSensor(coordinator, pack_index, "temp"))
    async_add_entities(entities)


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
    def native_value(self) -> float | str | datetime | None:
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


class EcoflowBatteryPackSensor(CoordinatorEntity[EcoflowOceanCoordinator], SensorEntity):
    """Per-battery-pack SOC, power, or temperature sensor."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EcoflowOceanCoordinator,
        pack_index: int,
        metric: str,
    ) -> None:
        super().__init__(coordinator)
        self._pack_index = pack_index
        self._metric = metric
        sn = coordinator.serial_number
        self._attr_unique_id = f"{sn}_battery_pack_{pack_index}_{metric}"
        if metric == "soc":
            self._attr_name = f"Battery pack {pack_index} SOC"
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif metric == "power":
            self._attr_name = f"Battery pack {pack_index} power"
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
        else:
            self._attr_name = f"Battery pack {pack_index} temperature"
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def device_info(self) -> DeviceInfo:
        pack = self._pack()
        pack_sn = pack.get("sn") if pack else None
        if pack_sn:
            return DeviceInfo(
                identifiers={(DOMAIN, pack_sn)},
                name=f"Battery pack {self._pack_index} ({pack_sn[-6:]})",
                manufacturer=MANUFACTURER,
                model="Ocean Battery Pack",
                serial_number=pack_sn,
                via_device=(DOMAIN, self.coordinator.serial_number),
            )
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL_POWER_OCEAN,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def native_value(self) -> float | None:
        pack = self._pack()
        if not pack:
            return None
        key = {"soc": "soc", "power": "power_w", "temp": "temp_c"}[self._metric]
        value = pack.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        pack = self._pack()
        if not pack:
            return None
        attrs: dict[str, Any] = {}
        for key in (
            "sn",
            "slot",
            "soh",
            "voltage_v",
            "current_a",
            "remain_wh",
            "cell_temp_max_c",
            "cell_temp_min_c",
        ):
            if pack.get(key) is not None:
                attrs[key] = pack[key]
        return attrs or None

    @property
    def available(self) -> bool:
        if not super().available or not self.coordinator.data:
            return False
        return self.native_value is not None

    def _pack(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not isinstance(data, EcoflowOceanState):
            return None
        for pack in data.battery_packs:
            if isinstance(pack, dict) and pack.get("index") == self._pack_index:
                return pack
        if 1 <= self._pack_index <= len(data.battery_packs):
            pack = data.battery_packs[self._pack_index - 1]
            return pack if isinstance(pack, dict) else None
        return None
