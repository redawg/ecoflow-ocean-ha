"""Typed models for EcoFlow Power Ocean telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class EcoflowDevice:
    """A Power Ocean inverter or related device from account discovery."""

    serial_number: str
    name: str
    product_type: str = "power_ocean"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EcoflowOceanState:
    """Parsed live system state for one Power Ocean installation."""

    serial_number: str
    battery_soc: float | None = None
    battery_power_w: float | None = None
    solar_power_w: float | None = None
    grid_power_w: float | None = None
    home_power_w: float | None = None
    status: str | None = None
    phase_a_voltage_v: float | None = None
    phase_a_current_a: float | None = None
    phase_a_power_w: float | None = None
    phase_b_voltage_v: float | None = None
    phase_b_current_a: float | None = None
    phase_b_power_w: float | None = None
    phase_c_voltage_v: float | None = None
    phase_c_current_a: float | None = None
    phase_c_power_w: float | None = None
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
            "phase_a_voltage_v",
            "phase_a_current_a",
            "phase_a_power_w",
            "phase_b_voltage_v",
            "phase_b_current_a",
            "phase_b_power_w",
            "phase_c_voltage_v",
            "phase_c_current_a",
            "phase_c_power_w",
            "updated_at",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value.isoformat() if isinstance(value, datetime) else value
        return data
