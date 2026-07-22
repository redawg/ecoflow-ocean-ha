"""Map EcoFlow Power Ocean API payloads to typed models."""

from __future__ import annotations

import json
import time
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

# Grid/battery/home power flow numbers change sign and magnitude on a
# minute-by-minute basis (export vs import, charge vs discharge), unlike
# mostly-static fields such as SOC or work mode. The device doesn't emit
# every field on every MQTT message — the site grid meter and per-pack
# battery snapshots each ride their own, less frequent frame types, and
# protobuf frames omit fields that are exactly zero — so a merged telemetry
# cache can otherwise keep echoing a stale "exporting 3kW" or "discharging"
# reading long after reality has moved on (observed: tens of minutes).
# Expire just these volatile keys if they haven't been refreshed recently,
# instead of letting stale extremes masquerade as live data indefinitely.
STALE_POWER_FIELD_MAX_AGE_S = 20.0
VOLATILE_POWER_FIELDS: frozenset[str] = frozenset(
    {
        *_GRID_POWER_KEYS,
        "grid_power_w",
        *_BATTERY_POWER_KEYS,
        "battery_power_w",
        *_HOME_POWER_KEYS,
        "home_power_w",
        "pcs_act_pwr",
    }
)


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


def _normalize_work_mode(raw: str | int | float | None) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        from .const import EMS_WORK_MODE_CODES

        return EMS_WORK_MODE_CODES.get(int(raw), f"mode_{int(raw)}")
    text = str(raw)
    if text.isdigit():
        from .const import EMS_WORK_MODE_CODES

        return EMS_WORK_MODE_CODES.get(int(text), f"mode_{text}")
    return EMS_WORK_MODES.get(text, text.removeprefix("WORKMODE_").lower())


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


def _flatten_mqtt_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten EcoFlow MQTT JSON params (cmdFunc/cmdId/typeCode prefixes)."""
    flat: dict[str, Any] = {}
    prefix = ""
    if "typeCode" in raw:
        prefix = f"{raw['typeCode']}."
    elif "cmdFunc" in raw and "cmdId" in raw:
        prefix = f"{raw['cmdFunc']}_{raw['cmdId']}."

    containers: list[dict[str, Any]] = []
    for key in ("param", "params"):
        value = raw.get(key)
        if isinstance(value, dict):
            containers.append(value)

    if not containers and isinstance(raw.get("data"), dict):
        data = raw["data"]
        if raw.get("operateType") == "latestQuotas" and isinstance(data.get("quotaMap"), dict):
            return dict(data["quotaMap"])
        if isinstance(data.get("quota"), dict):
            containers.append(data["quota"])
        for key, value in data.items():
            if key not in {"quota", "quotaMap", "online"} and not isinstance(value, dict):
                flat[key] = value

    for container in containers:
        for key, value in container.items():
            flat_key = f"{prefix}{key}" if prefix else key
            flat[flat_key] = value
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    flat[f"{flat_key}.{sub_key}"] = sub_value
                    if isinstance(sub_value, dict):
                        for sub_sub_key, sub_sub_value in sub_value.items():
                            flat[f"{flat_key}.{sub_key}.{sub_sub_key}"] = sub_sub_value

    for key, value in raw.items():
        if key in {"param", "params", "data", "cmdFunc", "cmdId", "typeCode"}:
            continue
        if key not in flat:
            flat[key] = value

    return flat


def parse_mqtt_payload(
    payload: bytes, serial: str | None = None, product_type: str | None = None
) -> dict[str, Any] | None:
    """Extract a flat telemetry dict from an EcoFlow MQTT message payload."""
    if not payload:
        return None

    try:
        text = payload.decode("utf-8")
        raw = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raw = None

    if isinstance(raw, dict):
        flat = _flatten_mqtt_params(raw)
        return flat or None

    from .const import PRODUCT_TYPE_EV_CHARGER, PRODUCT_TYPE_OCEAN_PANEL
    from .ev_charger_decoder import parse_ev_charger_payload
    from .panel_decoder import parse_ocean_panel_payload
    from .protobuf_decoder import parse_protobuf_payload

    if product_type == PRODUCT_TYPE_OCEAN_PANEL:
        panel_flat = parse_ocean_panel_payload(payload)
        if panel_flat:
            return panel_flat
    if product_type == PRODUCT_TYPE_EV_CHARGER:
        ev_flat = parse_ev_charger_payload(payload)
        if ev_flat:
            return ev_flat

    from .inverter_decoder import parse_ocean_inverter_payload

    inverter_wire = parse_ocean_inverter_payload(payload)
    inverter_flat = parse_protobuf_payload(payload, serial)
    if inverter_wire or inverter_flat:
        merged: dict[str, Any] = {}
        if inverter_flat:
            merged.update(inverter_flat)
        if inverter_wire:
            merged.update(inverter_wire)
        return merged

    panel_flat = parse_ocean_panel_payload(payload)
    if panel_flat:
        return panel_flat
    return parse_ev_charger_payload(payload)


def parse_flat_telemetry(serial_number: str, flat: dict[str, Any]) -> EcoflowOceanState:
    """Parse accumulated flat MQTT telemetry into EcoflowOceanState."""
    merged = dict(flat)

    # latestQuotas / heartbeat keys may use cmdFunc prefixes — resolve aliases.
    for key, aliases in (
        ("bpSoc", _SOC_KEYS),
        ("bpPwr", _BATTERY_POWER_KEYS),
        ("mpptPwr", _SOLAR_POWER_KEYS),
        ("sysGridPwr", _GRID_POWER_KEYS),
        ("sysLoadPwr", _HOME_POWER_KEYS),
    ):
        if merged.get(key) is None:
            for alias in aliases:
                if alias in merged:
                    merged[key] = merged[alias]
                    break
        for alias in aliases:
            prefixed = next((k for k in merged if k.endswith(f".{alias}") or k.endswith(f"_{alias}")), None)
            if prefixed and merged.get(key) is None:
                merged[key] = merged[prefixed]
                break

    # Phase metrics from nested or prefixed keys.
    for phase, prefix in (("a", "pcsAPhase"), ("b", "pcsBPhase"), ("c", "pcsCPhase")):
        block = merged.get(prefix)
        if not isinstance(block, dict):
            block = next(
                (merged[k] for k in merged if k.endswith(f".{prefix}") or k.endswith(prefix)),
                None,
            )
        if isinstance(block, dict):
            merged[f"{prefix}.vol"] = block.get("vol")
            merged[f"{prefix}.amp"] = block.get("amp")
            merged[f"{prefix}.actPwr"] = block.get("actPwr", block.get("pwr"))

    quota: dict[str, Any] = {}
    energy_report = {
        k: merged[k]
        for k in ("bpSoc", "bpPwr", "mpptPwr", "sysGridPwr", "sysLoadPwr", "online")
        if k in merged
    }
    if energy_report:
        quota["JTS1_ENERGY_STREAM_REPORT"] = energy_report

    change_report = {
        k: merged[k]
        for k in (
            "emsWordMode",
            "sysBatChgUpLimit",
            "sysBatDsgDownLimit",
            "emsFeedPwr",
            "emsFeedRatio",
        )
        if k in merged
    }
    if change_report:
        quota["JTS1_EMS_CHANGE_REPORT"] = change_report

    heartbeat: dict[str, Any] = {}
    for phase_key in ("pcsAPhase", "pcsBPhase", "pcsCPhase"):
        phase = {}
        for field in ("vol", "amp", "actPwr", "pwr"):
            val = merged.get(f"{phase_key}.{field}")
            if val is not None:
                phase[field if field != "pwr" else "actPwr"] = val
        if phase:
            heartbeat[phase_key] = phase
    if heartbeat:
        quota["JTS1_EMS_HEARTBEAT"] = heartbeat

    response = {"data": {**energy_report, "quota": quota}}
    try:
        state = parse_system_state(serial_number, response)
    except ApiNotMappedError:
        state = EcoflowOceanState(
            serial_number=serial_number,
            solar_power_w=_first_number(merged, _SOLAR_POWER_KEYS),
            battery_soc=_first_number(merged, _SOC_KEYS),
            battery_power_w=_first_number(merged, _BATTERY_POWER_KEYS),
            grid_power_w=_first_number(merged, _GRID_POWER_KEYS),
            home_power_w=_first_number(merged, _HOME_POWER_KEYS),
            updated_at=datetime.now(tz=timezone.utc),
            raw={"merged": merged},
        )

    mppt_power: dict[int, float] = {}
    mppt_active: dict[int, bool] = {}
    for idx in range(1, 6):
        power = merged.get(f"mppt_string_{idx}_power_w")
        if power is not None:
            try:
                mppt_power[idx] = float(power)
            except (TypeError, ValueError):
                pass
        active = merged.get(f"mppt_string_{idx}_active")
        if active is not None:
            mppt_active[idx] = bool(active)
        elif idx in mppt_power:
            mppt_active[idx] = abs(mppt_power[idx]) > 5
    if mppt_power:
        state.mppt_string_power_w = mppt_power
        state.mppt_string_active = mppt_active
        if state.solar_power_w is None:
            state.solar_power_w = sum(max(v, 0.0) for v in mppt_power.values())

    packs = merged.get("bp_packs")
    if isinstance(packs, list) and packs:
        state.battery_packs = [p for p in packs if isinstance(p, dict)]

    pcs_act_pwr = merged.get("pcs_act_pwr")
    if pcs_act_pwr is not None:
        try:
            state.pcs_act_pwr = float(pcs_act_pwr)
        except (TypeError, ValueError):
            pass
    return state


def merge_telemetry(
    existing: dict[str, Any],
    update: dict[str, Any],
    *,
    field_ts: dict[str, float] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Merge MQTT telemetry updates, ignoring null/zero-only heartbeat noise when stale.

    When the caller supplies ``field_ts`` (a per-serial dict it keeps around
    across calls), volatile power-flow fields (see ``VOLATILE_POWER_FIELDS``)
    are timestamped as they're refreshed and dropped once they exceed
    ``STALE_POWER_FIELD_MAX_AGE_S`` without an update, so a reading from
    30 minutes ago can't keep being reported as the current grid/battery
    status. Fields not in that set (SOC, work mode, etc.) are unaffected and
    keep the previous "last known value never expires" behaviour.
    """
    merged = dict(existing)
    now = now if now is not None else time.time()
    for key, value in update.items():
        if value is None:
            continue
        if key == "bp_packs" and isinstance(value, list):
            merged[key] = _merge_battery_packs(merged.get(key), value)
            continue
        merged[key] = value
        if field_ts is not None and key in VOLATILE_POWER_FIELDS:
            field_ts[key] = now

    if field_ts is not None:
        for key in list(merged):
            if key not in VOLATILE_POWER_FIELDS:
                continue
            last_seen = field_ts.get(key)
            if last_seen is None or now - last_seen > STALE_POWER_FIELD_MAX_AGE_S:
                merged.pop(key, None)
                field_ts.pop(key, None)

    return merged


def _merge_battery_packs(existing: Any, update: list[Any]) -> list[dict[str, Any]]:
    by_sn: dict[str, dict[str, Any]] = {}
    if isinstance(existing, list):
        for pack in existing:
            if isinstance(pack, dict) and pack.get("sn"):
                by_sn[str(pack["sn"])] = dict(pack)
    for pack in update:
        if not isinstance(pack, dict) or not pack.get("sn"):
            continue
        sn = str(pack["sn"])
        prev = by_sn.get(sn, {})
        merged_pack = dict(prev)
        for key, value in pack.items():
            if value is not None:
                merged_pack[key] = value
        by_sn[sn] = merged_pack
    packs = sorted(
        by_sn.values(),
        key=lambda p: (p.get("slot") is None, p.get("slot") or 0, str(p.get("sn") or "")),
    )
    for idx, pack in enumerate(packs, start=1):
        pack["index"] = idx
    return packs


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
        # Sign convention (matches inverter_decoder.py / web hub.py):
        # grid negative = export, battery positive = charging. Subtracting
        # battery adds discharge power into the home estimate and removes
        # power that's going into charging instead of the house.
        battery = state.battery_power_w or 0.0
        state.home_power_w = state.solar_power_w + state.grid_power_w - battery

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
