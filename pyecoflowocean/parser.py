"""Map raw EcoFlow API payloads to typed models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pyecoflowocean.exceptions import ApiNotMappedError
from pyecoflowocean.models import EcoflowDevice, EcoflowOceanState

# Candidate JSON keys seen in other EcoFlow integrations — replace after capture.
_SOC_KEYS = ("soc", "batterySoc", "bpSoc", "emsBatterySoc")
_BATTERY_POWER_KEYS = ("bpPwr", "batteryPower", "bpPower", "batPower")
_SOLAR_POWER_KEYS = ("pvPower", "solarPower", "mpptPower", "pv1Power")
_GRID_POWER_KEYS = ("gridPower", "sysGridPower", "gridPwr", "toGridPower")
_HOME_POWER_KEYS = ("loadPower", "homePower", "sysLoadPower", "outputPower")
_STATUS_KEYS = ("status", "sysStatus", "workMode", "systemStatus")


def _first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        return str(value)
    return None


def parse_device(raw: dict[str, Any]) -> EcoflowDevice | None:
    """Parse one device record from discovery response."""
    serial = (
        raw.get("sn")
        or raw.get("serialNumber")
        or raw.get("deviceSn")
        or raw.get("device_sn")
    )
    if not serial:
        return None

    name = raw.get("deviceName") or raw.get("name") or str(serial)
    product = raw.get("productType") or raw.get("deviceType") or raw.get("type") or "unknown"
    return EcoflowDevice(
        serial_number=str(serial),
        name=str(name),
        product_type=str(product),
        raw=raw,
    )


def parse_system_state(serial_number: str, payload: dict[str, Any]) -> EcoflowOceanState:
    """Parse telemetry payload into EcoflowOceanState.

    Raises ApiNotMappedError when no known telemetry keys are present.
    """
    if not payload:
        raise ApiNotMappedError("Empty telemetry payload")

    state = EcoflowOceanState(
        serial_number=serial_number,
        battery_soc=_first_number(payload, _SOC_KEYS),
        battery_power_w=_first_number(payload, _BATTERY_POWER_KEYS),
        solar_power_w=_first_number(payload, _SOLAR_POWER_KEYS),
        grid_power_w=_first_number(payload, _GRID_POWER_KEYS),
        home_power_w=_first_number(payload, _HOME_POWER_KEYS),
        status=_first_string(payload, _STATUS_KEYS),
        updated_at=datetime.now(tz=UTC),
        raw=payload,
    )

    if all(
        value is None
        for value in (
            state.battery_soc,
            state.battery_power_w,
            state.solar_power_w,
            state.grid_power_w,
            state.home_power_w,
            state.status,
        )
    ):
        raise ApiNotMappedError(
            "Telemetry keys not recognized. Run discover_devices.py with "
            "ECOFLOW_DUMP_JSON=1 after mapping parser fields in docs/api-notes.md."
        )

    return state
