"""Typed models for EcoFlow Power Ocean telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EcoflowDevice:
    """A Power Ocean inverter or related device from account discovery."""

    serial_number: str
    name: str
    product_type: str = "power_ocean"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EcoflowPanelState:
    """Parsed live state for an EcoFlow Ocean Smart Panel 40."""

    serial_number: str
    grid_voltage_v: float | None = None
    grid_voltage_l1_v: float | None = None
    grid_voltage_l2_v: float | None = None
    home_power_w: float | None = None
    inverter_feed_power_w: float | None = None
    grid_import_power_w: float | None = None
    master_grid_power_w: float | None = None
    channel_sum_power_w: float | None = None
    hall_total_power_w: float | None = None
    # hall_total_power_w (967) − channel_sum_power_w (966): the Smart Panel's
    # own parasitic draw (relays/electronics/display) to run itself, not a
    # branch-circuit load. User-confirmed field semantics, 2026-07-18.
    panel_self_consumption_w: float | None = None
    backup_reserve_soc: float | None = None
    solar_backup_reserve_soc: float | None = None
    storm_enabled: bool | None = None
    linked_ev_charger_serial: str | None = None
    ev_charge_power_w: float | None = None
    online: bool | None = None
    circuit_power_w: dict[int, float] = field(default_factory=dict)
    circuit_voltage_v: dict[int, float] = field(default_factory=dict)
    circuit_active: dict[int, bool] = field(default_factory=dict)
    circuit_names: dict[int, str] = field(default_factory=dict)
    circuit_set_amp: dict[int, float] = field(default_factory=dict)
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"serial_number": self.serial_number}
        for key in (
            "grid_voltage_v",
            "grid_voltage_l1_v",
            "grid_voltage_l2_v",
            "home_power_w",
            "inverter_feed_power_w",
            "grid_import_power_w",
            "master_grid_power_w",
            "channel_sum_power_w",
            "hall_total_power_w",
            "panel_self_consumption_w",
            "backup_reserve_soc",
            "solar_backup_reserve_soc",
            "storm_enabled",
            "linked_ev_charger_serial",
            "ev_charge_power_w",
            "online",
            "updated_at",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value.isoformat() if isinstance(value, datetime) else value
        if self.circuit_power_w:
            data["circuit_power_w"] = self.circuit_power_w
        if self.circuit_names:
            data["circuit_names"] = self.circuit_names
        return data


@dataclass
class EcoflowEvChargerState:
    """Parsed live state for an EcoFlow Ocean EV Charger (PowerPulse)."""

    serial_number: str
    charge_power_w: float | None = None
    output_voltage_v: float | None = None
    max_current_a: float | None = None
    max_power_w: float | None = None
    charge_limit_percent: float | None = None
    vehicle_connected: bool | None = None
    charging_active: bool | None = None
    online: bool | None = None
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"serial_number": self.serial_number}
        for key in (
            "charge_power_w",
            "output_voltage_v",
            "max_current_a",
            "max_power_w",
            "charge_limit_percent",
            "vehicle_connected",
            "charging_active",
            "online",
            "updated_at",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value.isoformat() if isinstance(value, datetime) else value
        return data


@dataclass
class EcoflowOceanState:
    """Parsed live system state for one Power Ocean installation."""

    serial_number: str
    battery_soc: float | None = None
    battery_power_w: float | None = None
    solar_power_w: float | None = None
    grid_power_w: float | None = None
    home_power_w: float | None = None
    status: str | None = None
    work_mode: str | None = None
    backup_soc_limit: float | None = None
    discharge_soc_limit: float | None = None
    feed_power_limit_w: float | None = None
    feed_ratio_percent: float | None = None
    online: bool | None = None
    phase_a_voltage_v: float | None = None
    phase_a_current_a: float | None = None
    phase_a_power_w: float | None = None
    phase_b_voltage_v: float | None = None
    phase_b_current_a: float | None = None
    phase_b_power_w: float | None = None
    phase_c_voltage_v: float | None = None
    phase_c_current_a: float | None = None
    phase_c_power_w: float | None = None
    pcs_act_pwr: float | None = None
    mppt_string_power_w: dict[int, float] = field(default_factory=dict)
    mppt_string_active: dict[int, bool] = field(default_factory=dict)
    battery_packs: list[dict[str, Any]] = field(default_factory=list)
    updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a flat dict of non-null telemetry fields."""
        data: dict[str, Any] = {"serial_number": self.serial_number}
        for key in (
            "battery_soc",
            "battery_power_w",
            "solar_power_w",
            "grid_power_w",
            "home_power_w",
            "status",
            "work_mode",
            "backup_soc_limit",
            "discharge_soc_limit",
            "feed_power_limit_w",
            "feed_ratio_percent",
            "online",
            "phase_a_voltage_v",
            "phase_a_current_a",
            "phase_a_power_w",
            "phase_b_voltage_v",
            "phase_b_current_a",
            "phase_b_power_w",
            "phase_c_voltage_v",
            "phase_c_current_a",
            "phase_c_power_w",
            "pcs_act_pwr",
            "updated_at",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value.isoformat() if isinstance(value, datetime) else value
        if self.mppt_string_power_w:
            data["mppt_string_power_w"] = self.mppt_string_power_w
        if self.mppt_string_active:
            data["mppt_string_active"] = self.mppt_string_active
        if self.battery_packs:
            data["battery_packs"] = self.battery_packs
        return data
