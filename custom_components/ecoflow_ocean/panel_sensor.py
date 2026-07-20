"""Sensor platform for EcoFlow Ocean Smart Panel 40."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL_OCEAN_PANEL
from .coordinator import EcoflowOceanCoordinator
from .pyecoflowocean import EcoflowOceanState, EcoflowPanelState
from .pyecoflowocean.overhead import measure_inverter_overhead_w


@dataclass(frozen=True, slots=True)
class PanelSensorDefinition:
    """Definition for one Ocean Panel sensor entity."""

    key: str
    name: str
    value_fn: Callable[[EcoflowPanelState], float | None]
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None


PANEL_SENSOR_DEFINITIONS: tuple[PanelSensorDefinition, ...] = (
    PanelSensorDefinition(
        key="home_power",
        name="Total load",
        value_fn=lambda s: s.home_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PanelSensorDefinition(
        key="grid_voltage",
        name="Grid voltage",
        value_fn=lambda s: s.grid_voltage_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    PanelSensorDefinition(
        key="grid_voltage_l1",
        name="Grid voltage L1",
        value_fn=lambda s: s.grid_voltage_l1_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    PanelSensorDefinition(
        key="grid_voltage_l2",
        name="Grid voltage L2",
        value_fn=lambda s: s.grid_voltage_l2_v,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    PanelSensorDefinition(
        key="grid_import_power",
        name="Grid import power",
        value_fn=lambda s: s.grid_import_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PanelSensorDefinition(
        key="master_grid_power",
        name="Master grid power",
        value_fn=lambda s: s.master_grid_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PanelSensorDefinition(
        key="channel_sum_power",
        name="Channel sum power",
        value_fn=lambda s: s.channel_sum_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PanelSensorDefinition(
        key="hall_total_power",
        name="Hall total power",
        value_fn=lambda s: s.hall_total_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PanelSensorDefinition(
        key="panel_self_consumption",
        name="Panel self-consumption",
        value_fn=lambda s: s.panel_self_consumption_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PanelSensorDefinition(
        key="backup_reserve_soc",
        name="Backup reserve SOC",
        value_fn=lambda s: s.backup_reserve_soc,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    PanelSensorDefinition(
        key="solar_backup_reserve_soc",
        name="Solar backup reserve SOC",
        value_fn=lambda s: s.solar_backup_reserve_soc,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    PanelSensorDefinition(
        key="ev_charge_power",
        name="EV charge power",
        value_fn=lambda s: s.ev_charge_power_w,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
)

MAX_CIRCUITS = 40


async def async_setup_panel_sensors(
    coordinator: EcoflowOceanCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Ocean Panel sensors."""
    entities: list[SensorEntity] = [
        EcoflowPanelSensor(coordinator, definition) for definition in PANEL_SENSOR_DEFINITIONS
    ]
    for circuit in range(1, MAX_CIRCUITS + 1):
        entities.append(EcoflowPanelCircuitSensor(coordinator, circuit))
    entities.append(EcoflowInverterOverheadSensor(coordinator))
    async_add_entities(entities)


class EcoflowPanelSensor(CoordinatorEntity[EcoflowOceanCoordinator], SensorEntity):
    """EcoFlow Ocean Panel system sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EcoflowOceanCoordinator,
        definition: PanelSensorDefinition,
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
            model=MODEL_OCEAN_PANEL,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def native_value(self) -> float | None:
        state = self._panel_state()
        if state is None:
            return None
        return self._definition.value_fn(state)

    @property
    def available(self) -> bool:
        state = self._panel_state()
        if not super().available or state is None:
            return False
        return self._definition.value_fn(state) is not None

    def _panel_state(self) -> EcoflowPanelState | None:
        data = self.coordinator.data
        return data if isinstance(data, EcoflowPanelState) else None


class EcoflowInverterOverheadSensor(CoordinatorEntity[EcoflowOceanCoordinator], SensorEntity):
    """Estimated inverter PCS/fan draw that never reaches the panel feed CT.

    Cross-device derived sensor: combines this Smart Panel's own
    `inverter_feed_power_w` reading with the sibling Power Ocean inverter's
    `solar_power_w` / `battery_power_w` (solar − battery − feed). It's set
    up on the panel platform because it needs the panel's feed reading to
    exist, but it describes the inverter's own conversion/cooling loss —
    hence the descriptive name rather than attributing it to either device.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_name = "Inverter conversion & fan loss (est.)"

    def __init__(self, coordinator: EcoflowOceanCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_inverter_overhead"
        self._extra_remove: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        inverter = self._inverter_coordinator()
        if inverter is not None:
            self._extra_remove = inverter.async_add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._extra_remove is not None:
            self._extra_remove()
            self._extra_remove = None
        await super().async_will_remove_from_hass()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL_OCEAN_PANEL,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def native_value(self) -> float | None:
        panel = self._panel_state()
        inverter = self._inverter_state()
        if panel is None or inverter is None:
            return None
        return measure_inverter_overhead_w(
            solar_w=inverter.solar_power_w,
            battery_w=inverter.battery_power_w,
            feed_w=panel.inverter_feed_power_w,
        )

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    def _panel_state(self) -> EcoflowPanelState | None:
        data = self.coordinator.data
        return data if isinstance(data, EcoflowPanelState) else None

    def _inverter_coordinator(self) -> EcoflowOceanCoordinator | None:
        for entry_coordinator in self.hass.data.get(DOMAIN, {}).values():
            if (
                isinstance(entry_coordinator, EcoflowOceanCoordinator)
                and entry_coordinator is not self.coordinator
                and isinstance(entry_coordinator.data, EcoflowOceanState)
            ):
                return entry_coordinator
        return None

    def _inverter_state(self) -> EcoflowOceanState | None:
        inverter = self._inverter_coordinator()
        return inverter.data if inverter is not None else None


class EcoflowPanelCircuitSensor(CoordinatorEntity[EcoflowOceanCoordinator], SensorEntity):
    """Per-circuit power sensor for Ocean Panel."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: EcoflowOceanCoordinator, circuit: int) -> None:
        super().__init__(coordinator)
        self._circuit = circuit
        sn = coordinator.serial_number
        self._attr_unique_id = f"{sn}_circuit_{circuit:02d}_power"
        self._attr_name = f"Circuit {circuit} power"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=MODEL_OCEAN_PANEL,
            serial_number=self.coordinator.serial_number,
        )

    @property
    def native_value(self) -> float | None:
        state = self._panel_state()
        if state is None:
            return None
        return state.circuit_power_w.get(self._circuit)

    @property
    def extra_state_attributes(self) -> dict[str, str | float | bool] | None:
        state = self._panel_state()
        if state is None:
            return None
        attrs: dict[str, str | float | bool] = {}
        name = state.circuit_names.get(self._circuit)
        if name:
            attrs["circuit_name"] = name
        voltage = state.circuit_voltage_v.get(self._circuit)
        if voltage is not None:
            attrs["voltage_v"] = voltage
        active = state.circuit_active.get(self._circuit)
        if active is not None:
            attrs["active"] = active
        set_amp = state.circuit_set_amp.get(self._circuit)
        if set_amp is not None:
            attrs["set_amp"] = set_amp
        return attrs or None

    @property
    def available(self) -> bool:
        state = self._panel_state()
        if not super().available or state is None:
            return False
        return self._circuit in state.circuit_power_w

    def _panel_state(self) -> EcoflowPanelState | None:
        data = self.coordinator.data
        return data if isinstance(data, EcoflowPanelState) else None
