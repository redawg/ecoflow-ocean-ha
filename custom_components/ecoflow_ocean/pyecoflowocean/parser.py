"""Map EcoFlow Power Ocean API payloads to typed models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .const import EMS_WORK_MODES
from .exceptions import ApiNotMappedError
from .models import EcoflowDevice, EcoflowOceanState

REPORT_PRIORITY = (
    "ParallelEnergyStreamReport",
    "JTS1_ENERGY_STREAM_REPORT",
    "JTS1_EMS_HEARTBEAT",
    "JTS1_EMS_CHANGE_REPORT",
    "data",
)

_SOC_KEYS = ("bpSoc", "soc", "batterySoc", "emsBatterySoc", "ocean_bpSoc")
_BATTERY_POWER_KEYS = ("bpPwr", "emsBpPower", "batteryPower", "ocean_bpPwr")
_SOLAR_POWER_KEYS = ("mpptPwr", "pvPower", "solarPower", "pvInvPwr", "ocean_mpptPwr")
_GRID_POWER_KEYS = ("sysGridPwr", "gridPower", "pcsMeterPower", "ocean_sysGridPwr")
_HOME_POWER_KEYS = ("sysLoadPwr", "loadPower", "homePower", "ocean_sysLoadPwr")
_STATUS_KEYS = ("emsWordMode", "pcsRunSta", "sysWorkSta", "status")
_WORK_MODE_KEYS = ("emsWordMode",)
_CHARGE_LIMIT_KEYS = ("sysBatChgUpLimit",)
_DISCHARGE_LIMIT_KEYS = ("sysBatDsgDownLimit",)
_FEED_POWER_KEYS = ("emsFeedPwr",)
_FEED_RATIO_KEYS = ("emsFeedRatio",)


def _first_number(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_string(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        return str(value)
    return None


def _maybe_parse_json(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def _collect_report_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [data]

    quota = data.get("quota")
    if isinstance(quota, dict):
        for report_name in REPORT_PRIORITY:
            report = quota.get(report_name)
            if isinstance(report, dict):
                blocks.append(report)

        for report_name, report in quota.items():
            if report_name in REPORT_PRIORITY or not isinstance(report, dict):
                continue
            blocks.append(report)

    parallel = data.get("parallel")
    if isinstance(parallel, dict):
        for report in parallel.values():
            report = _maybe_parse_json(report)
            if isinstance(report, dict):
                blocks.append(report)

    return blocks


def _merge_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for block in blocks:
        for key, value in block.items():
            if key in {"quota", "parallel"}:
                continue
            value = _maybe_parse_json(value)
            if key not in merged or merged[key] in (None, "", 0):
                merged[key] = value
    return merged


def _phase_metrics(blocks: list[dict[str, Any]]) -> dict[str, float | None]:
    for block in blocks:
        if "pcsAPhase" not in block:
            continue
        phase_a = block.get("pcsAPhase") or {}
        phase_b = block.get("pcsBPhase") or {}
        phase_c = block.get("pcsCPhase") or {}
        if not isinstance(phase_a, dict):
            continue
        return {
            "phase_a_voltage_v": _first_number(phase_a, ("vol",)),
            "phase_a_current_a": _first_number(phase_a, ("amp",)),
            "phase_a_power_w": _first_number(phase_a, ("actPwr", "pwr")),
            "phase_b_voltage_v": _first_number(phase_b, ("vol",))
            if isinstance(phase_b, dict)
            else None,
            "phase_b_current_a": _first_number(phase_b, ("amp",))
            if isinstance(phase_b, dict)
            else None,
            "phase_b_power_w": _first_number(phase_b, ("actPwr", "pwr"))
            if isinstance(phase_b, dict)
            else None,
            "phase_c_voltage_v": _first_number(phase_c, ("vol",))
            if isinstance(phase_c, dict)
            else None,
            "phase_c_current_a": _first_number(phase_c, ("amp",))
            if isinstance(phase_c, dict)
            else None,
            "phase_c_power_w": _first_number(phase_c, ("actPwr", "pwr"))
            if isinstance(phase_c, dict)
            else None,
        }
    return {}


def _normalize_work_mode(raw: str | None) -> str | None:
    if raw is None:
        return None
    return EMS_WORK_MODES.get(raw, raw.removeprefix("WORKMODE_").lower())


def parse_device(raw: dict[str, Any]) -> EcoflowDevice | None:
    serial = (
        raw.get("sn")
        or raw.get("serialNumber")
        or raw.get("deviceSn")
        or raw.get("device_sn")
    )
    if not serial:
        return None

    product_type = str(
        raw.get("productType")
        or raw.get("deviceType")
        or raw.get("type")
        or raw.get("product_type")
        or "83"
    )
    name = raw.get("deviceName") or raw.get("name") or str(serial)
    return EcoflowDevice(
        serial_number=str(serial),
        name=str(name),
        product_type=product_type,
        raw=raw,
    )


def parse_system_state(serial_number: str, response: dict[str, Any]) -> EcoflowOceanState:
    """Parse provider-service device detail response."""
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise ApiNotMappedError(f"Unexpected telemetry response: {response!r}")

    blocks = _collect_report_blocks(data)
    merged = _merge_blocks(blocks)
    phases = _phase_metrics(blocks)

    work_mode_raw = _first_string(merged, _WORK_MODE_KEYS)
    state = EcoflowOceanState(
        serial_number=serial_number,
        battery_soc=_first_number(merged, _SOC_KEYS),
        battery_power_w=_first_number(merged, _BATTERY_POWER_KEYS),
        solar_power_w=_first_number(merged, _SOLAR_POWER_KEYS),
        grid_power_w=_first_number(merged, _GRID_POWER_KEYS),
        home_power_w=_first_number(merged, _HOME_POWER_KEYS),
        status=_normalize_work_mode(work_mode_raw)
        or _first_string(merged, _STATUS_KEYS),
        work_mode=_normalize_work_mode(work_mode_raw),
        backup_soc_limit=_first_number(merged, _CHARGE_LIMIT_KEYS),
        discharge_soc_limit=_first_number(merged, _DISCHARGE_LIMIT_KEYS),
        feed_power_limit_w=_first_number(merged, _FEED_POWER_KEYS),
        feed_ratio_percent=_first_number(merged, _FEED_RATIO_KEYS),
        online=bool(_first_number(merged, ("online",)) == 1) if "online" in merged else None,
        updated_at=datetime.now(tz=timezone.utc),
        raw={"merged": merged, "response": response},
        **phases,
    )

    if state.home_power_w is None and state.grid_power_w is not None and state.solar_power_w is not None:
        battery = state.battery_power_w or 0.0
        state.home_power_w = state.solar_power_w + state.grid_power_w + battery

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
            "Telemetry keys not recognized in provider-service response. "
            "Run discover_devices.py with ECOFLOW_DUMP_JSON=1."
        )

    return state
