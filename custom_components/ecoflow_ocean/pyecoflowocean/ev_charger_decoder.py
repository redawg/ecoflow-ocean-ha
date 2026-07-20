"""Decode EcoFlow Ocean EV Charger (product type 99 / PowerPulse) MQTT payloads."""

from __future__ import annotations

import logging
from typing import Any

from .wire_decoder import decode_protobuf, get_float

_LOGGER = logging.getLogger(__name__)

# Live field mapping from C102ZA1AZH6G0018 captures.
_STATUS_ROOT = 1
_TELEMETRY_BLOCK = 1
_MAX_CURRENT_FIELD = 17
_MAX_POWER_FIELD = 18
_OUTPUT_VOLTAGE_FIELD = 24
_CHARGE_LIMIT_FIELD = 25
_CHARGE_POWER_FIELD = 28
_SESSION_BLOCK = 203
_SESSION_POWER_FIELDS = (2, 4, 10)
_SESSION_VOLTAGE_FIELD = 3
_VEHICLE_CONNECTED_FIELD = 101
_CHARGING_ACTIVE_FIELD = 102


def parse_ev_charger_payload(payload: bytes) -> dict[str, Any] | None:
    """Decode EV charger binary MQTT into a flat telemetry dict."""
    if not payload:
        return None

    try:
        tree = decode_protobuf(payload)
    except ValueError:
        return None

    if not tree:
        return None

    block = _find_block_in_tree(tree, _TELEMETRY_BLOCK, _TELEMETRY_BLOCK)
    if not isinstance(block, dict):
        block = tree

    flat: dict[str, Any] = {}

    max_current = _find_float_in_tree(tree, _MAX_CURRENT_FIELD)
    if max_current is not None and 0 < max_current <= 200:
        flat["max_current_a"] = max_current

    max_power = _find_float_in_tree(tree, _MAX_POWER_FIELD)
    if max_power is not None and 0 < max_power <= 50000:
        flat["max_power_w"] = max_power

    output_voltage = _find_float_in_tree(tree, _OUTPUT_VOLTAGE_FIELD)
    if output_voltage is not None and 0 < output_voltage <= 280:
        flat["output_voltage_v"] = output_voltage

    charge_limit = _find_float_in_tree(tree, _CHARGE_LIMIT_FIELD)
    if charge_limit is not None and 0 <= charge_limit <= 100:
        flat["charge_limit_percent"] = charge_limit

    charge_power = _find_float_in_tree(tree, _CHARGE_POWER_FIELD)
    if charge_power is not None and abs(charge_power) <= 50000:
        flat["charge_power_w"] = charge_power

    session = _find_block_in_tree(tree, _TELEMETRY_BLOCK, _SESSION_BLOCK)
    if isinstance(session, dict):
        for field_num in _SESSION_POWER_FIELDS:
            power = get_float(session, field_num)
            if power is not None and abs(power) <= 50000:
                flat.setdefault("charge_power_w", power)
        voltage = get_float(session, _SESSION_VOLTAGE_FIELD)
        if voltage is not None:
            if voltage > 280:
                voltage = voltage / 100.0
            if 0 < voltage <= 280:
                flat["output_voltage_v"] = voltage

    vehicle_connected = _find_int_in_tree(tree, _VEHICLE_CONNECTED_FIELD)
    if vehicle_connected is not None:
        flat["vehicle_connected"] = vehicle_connected >= 1

    charging_active = _find_int_in_tree(tree, _CHARGING_ACTIVE_FIELD)
    if charging_active is not None:
        flat["charging_active"] = charging_active >= 1

    if flat.get("charge_power_w") and flat.get("charging_active") is None:
        flat["charging_active"] = float(flat["charge_power_w"]) > 50

    if not flat:
        return None

    _LOGGER.debug("EV charger decoded %d fields", len(flat))
    return flat


def parse_ev_charger_flat_telemetry(serial_number: str, flat: dict[str, Any]):
    """Parse accumulated flat EV charger MQTT telemetry into EcoflowEvChargerState."""
    from datetime import datetime, timezone

    from .models import EcoflowEvChargerState

    online = flat.get("online")
    if online is not None:
        online = bool(online == 1 or online is True)

    return EcoflowEvChargerState(
        serial_number=serial_number,
        charge_power_w=_maybe_float(flat.get("charge_power_w")),
        output_voltage_v=_maybe_float(flat.get("output_voltage_v")),
        max_current_a=_maybe_float(flat.get("max_current_a")),
        max_power_w=_maybe_float(flat.get("max_power_w")),
        charge_limit_percent=_maybe_float(flat.get("charge_limit_percent")),
        vehicle_connected=_maybe_bool(flat.get("vehicle_connected")),
        charging_active=_maybe_bool(flat.get("charging_active")),
        online=online,
        updated_at=datetime.now(tz=timezone.utc),
        raw={"merged": flat},
    )


def _find_block_in_tree(
    node: dict[int, Any], *field_nums: int, depth: int = 0
) -> dict[int, Any] | None:
    if depth > 8:
        return None
    if not field_nums:
        return node if isinstance(node, dict) else None
    head, *rest = field_nums
    block = node.get(head) if isinstance(node, dict) else None
    if isinstance(block, dict):
        if not rest:
            return block
        return _find_block_in_tree(block, *rest, depth=depth + 1)
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, dict):
                found = _find_block_in_tree(value, *field_nums, depth=depth + 1)
                if found is not None:
                    return found
    return None


def _find_float_in_tree(node: dict[int, Any], field_num: int, depth: int = 0) -> float | None:
    if depth > 8:
        return None
    val = node.get(field_num) if isinstance(node, dict) else None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, dict):
                found = _find_float_in_tree(value, field_num, depth=depth + 1)
                if found is not None:
                    return found
    return None


def _find_int_in_tree(node: dict[int, Any], field_num: int, depth: int = 0) -> int | None:
    if depth > 8:
        return None
    val = node.get(field_num) if isinstance(node, dict) else None
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, dict):
                found = _find_int_in_tree(value, field_num, depth=depth + 1)
                if found is not None:
                    return found
    return None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
