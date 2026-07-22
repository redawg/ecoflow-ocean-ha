"""Decode EcoFlow Ocean Smart Panel (product type 95) MQTT payloads."""

from __future__ import annotations

import logging
from typing import Any

from .wire_decoder import decode_protobuf, get_float, unwrap_payload_root

_LOGGER = logging.getLogger(__name__)

# Ocean Panel 40: per-circuit live power in fields 1015–1054 (40 circuits).
CIRCUIT_FIELD_START = 1015
CIRCUIT_FIELD_END = 1054
CIRCUIT_POWER_FIELD = 2
CIRCUIT_VOLTAGE_FIELD = 1
CIRCUIT_ACTIVE_FIELD = 3

# Grid / aggregate voltage fields observed on HR61… installs.
GRID_VOLTAGE_FIELDS = (956, 957, 1063, 1064)
AGGREGATE_POWER_FIELDS = (964, 965, 966, 967)

# CDO Ocean Panel 40: breakers that feed the Ocean Pro inverter (split-phase).
# These are NOT branch-circuit house loads — exclude them from home_power.
INVERTER_FEED_CIRCUITS: frozenset[int] = frozenset({38, 40})
INVERTER_FEED_LABELS: dict[int, str] = {
    38: "Inverter feed L1",
    40: "Inverter feed L2",
}

# System settings observed on Ocean Panel 40 MQTT (HR61…).
BACKUP_RESERVE_SOC_FIELD = 1215
SOLAR_BACKUP_RESERVE_SOC_FIELD = 270
# Field 282 is present in settings dumps but does NOT track the app Storm
# Guard toggle (stayed at 2 through on/off cycles). Field 467 flipped 0↔1
# in lockstep with five Storm Guard toggles on 2026-07-20.
STORM_MODE_FIELD = 282  # raw / unknown semantics — keep for diagnostics
STORM_WATCH_FIELD = 467  # Storm Guard armed (0=off, 1=on)
STORM_ENABLED_FIELD = STORM_MODE_FIELD  # legacy alias used by capture scripts
LINKED_DEVICE_BLOCK = (1, 1)
CIRCUIT_CONFIG_FIELD_START = 794
CIRCUIT_CONFIG_FIELD_END = 947
CIRCUIT_NAME_FIELDS = (10, 8)  # nested under field 5
CIRCUIT_INDEX_FIELD = 2  # nested under field 2
CIRCUIT_LABEL_FIELD = 7  # nested under field 7


def parse_ocean_panel_payload(payload: bytes) -> dict[str, Any] | None:
    """Decode Ocean Panel binary MQTT into a flat telemetry dict."""
    if not payload:
        return None

    try:
        tree = decode_protobuf(payload)
    except ValueError:
        return None

    if not tree:
        return None

    root = unwrap_payload_root(tree)
    flat: dict[str, Any] = {}

    for field_num in range(CIRCUIT_FIELD_START, CIRCUIT_FIELD_END + 1):
        block = _find_block_in_tree(tree, field_num)
        if not isinstance(block, dict):
            continue
        channel = field_num - CIRCUIT_FIELD_START + 1
        power = get_float(block, CIRCUIT_POWER_FIELD)
        voltage = get_float(block, CIRCUIT_VOLTAGE_FIELD)
        active = get_float(block, CIRCUIT_ACTIVE_FIELD)
        if power is not None:
            flat[f"ch{channel}_power_w"] = power
        if voltage is not None and 80 <= voltage <= 280:
            flat[f"ch{channel}_voltage_v"] = voltage
        if active is not None:
            flat[f"ch{channel}_active"] = active >= 0.5

    grid_voltages = [
        v
        for field_num in GRID_VOLTAGE_FIELDS
        if (v := _find_float_in_tree(tree, field_num)) is not None and 80 <= v <= 280
    ]
    if grid_voltages:
        flat["grid_voltage_v"] = sum(grid_voltages) / len(grid_voltages)
        if len(grid_voltages) >= 2:
            flat["grid_voltage_l1_v"] = grid_voltages[0]
            flat["grid_voltage_l2_v"] = grid_voltages[1]

    agg_values: list[float | None] = []
    for idx, field_num in enumerate(AGGREGATE_POWER_FIELDS):
        val = _find_float_in_tree(tree, field_num)
        if val is not None and abs(val) < 50000:
            flat[f"aggregate_power_{idx + 1}_w"] = val
            agg_values.append(val)
        else:
            agg_values.append(None)

    if agg_values[0] is not None:
        flat["grid_import_power_w"] = agg_values[0]
    if len(agg_values) > 1 and agg_values[1] is not None:
        flat["master_grid_power_w"] = agg_values[1]
    if len(agg_values) > 2 and agg_values[2] is not None:
        flat["channel_sum_power_w"] = agg_values[2]
    if len(agg_values) > 3 and agg_values[3] is not None:
        flat["hall_total_power_w"] = agg_values[3]

    backup_soc = _find_float_in_tree(tree, BACKUP_RESERVE_SOC_FIELD)
    if backup_soc is not None and 0 <= backup_soc <= 100:
        flat["backup_reserve_soc"] = backup_soc

    solar_backup_soc = _find_float_in_tree(tree, SOLAR_BACKUP_RESERVE_SOC_FIELD)
    if solar_backup_soc is not None and 0 <= solar_backup_soc <= 100:
        flat["solar_backup_reserve_soc"] = solar_backup_soc

    storm_raw = _find_float_in_tree(tree, STORM_MODE_FIELD)
    if storm_raw is not None and storm_raw >= 0:
        flat["storm_mode"] = int(storm_raw)

    # App Storm Guard toggle — confirmed via 5× on/off MQTT capture.
    storm_watch = _find_float_in_tree(tree, STORM_WATCH_FIELD)
    if storm_watch is not None and storm_watch >= 0:
        flat["storm_watch"] = storm_watch >= 0.5

    # Active storm-event / charge-for-storm — not yet confirmed on this panel.
    # Older dumps used storm_mode==1 while Watch was discussed; keep as a
    # weak signal only when Watch is also on.
    if flat.get("storm_watch") and flat.get("storm_mode") == 1:
        flat["storm_enabled"] = True
    elif "storm_watch" in flat and not flat["storm_watch"]:
        flat["storm_enabled"] = False

    linked = _find_nested_block(tree, LINKED_DEVICE_BLOCK)
    if isinstance(linked, dict):
        ev_serial = linked.get(2)
        if isinstance(ev_serial, str) and ev_serial:
            flat["linked_ev_charger_serial"] = ev_serial

    circuit_names = _extract_circuit_names(tree)
    for channel, name in circuit_names.items():
        flat[f"ch{channel}_name"] = name

    for field_num in range(933, 948):
        block = _find_block_in_tree(tree, field_num)
        if isinstance(block, dict):
            amp = get_float(block, 3)
            if amp is not None and 0 < amp <= 200:
                flat[f"ch{field_num - 932}_set_amp"] = amp

    if not flat:
        return None

    _LOGGER.debug(
        "Ocean Panel decoded %d fields, circuits with power: %d",
        len(flat),
        sum(1 for k in flat if k.endswith("_power_w")),
    )
    return flat


def _find_block_in_tree(node: dict[int, Any], field_num: int, depth: int = 0) -> dict[int, Any] | None:
    if depth > 8:
        return None
    block = node.get(field_num)
    if isinstance(block, dict):
        return block
    for value in node.values():
        if isinstance(value, dict):
            found = _find_block_in_tree(value, field_num, depth + 1)
            if found is not None:
                return found
    return None


def _find_nested_block(node: dict[int, Any], field_nums: tuple[int, ...], depth: int = 0) -> dict[int, Any] | None:
    if depth > 8 or not field_nums:
        return None
    head, *rest = field_nums
    block = _find_block_in_tree(node, head)
    if block is None:
        return None
    if not rest:
        return block
    return _find_nested_block(block, tuple(rest), depth=depth + 1)


def _extract_circuit_names(tree: dict[int, Any]) -> dict[int, str]:
    names: dict[int, str] = {}
    for field_num in range(CIRCUIT_CONFIG_FIELD_START, CIRCUIT_CONFIG_FIELD_END + 1):
        block = _find_block_in_tree(tree, field_num)
        if not isinstance(block, dict):
            continue
        channel = _circuit_index_from_config(block, field_num)
        if channel is None:
            continue
        name = _circuit_name_from_config(block)
        if name:
            names[channel] = name
    return names


def _circuit_index_from_config(block: dict[int, Any], field_num: int) -> int | None:
    # Split-phase / linked channel: LoadSplitPhaseCfg.link_ch = 2
    index_block = block.get(CIRCUIT_INDEX_FIELD)
    if isinstance(index_block, dict):
        link_ch = index_block.get(2)
        if isinstance(link_ch, (int, float)) and 1 <= int(link_ch) <= MAX_CIRCUITS:
            return int(link_ch)
        index_val = index_block.get(CIRCUIT_INDEX_FIELD)
        if isinstance(index_val, (int, float)) and 1 <= int(index_val) <= MAX_CIRCUITS:
            return int(index_val)
    if isinstance(index_block, (int, float)) and 1 <= int(index_block) <= MAX_CIRCUITS:
        return int(index_block)

    # Primary config bank: fields 794..833 → circuits 1..40
    if CIRCUIT_CONFIG_FIELD_START <= field_num <= CIRCUIT_CONFIG_FIELD_START + MAX_CIRCUITS - 1:
        return field_num - CIRCUIT_CONFIG_FIELD_START + 1

    # Secondary bank observed on Panel 40 (908..947 → 1..40)
    secondary_start = 908
    if secondary_start <= field_num <= secondary_start + MAX_CIRCUITS - 1:
        return field_num - secondary_start + 1

    return None


def _circuit_name_from_config(block: dict[int, Any]) -> str | None:
    name_field = block.get(5)
    if isinstance(name_field, str) and name_field.strip():
        return name_field.strip()
    if isinstance(name_field, dict):
        strings: list[str] = []
        _collect_strings(name_field, strings)
        # Also check known nested name field numbers.
        for field_num in CIRCUIT_NAME_FIELDS:
            nested = name_field.get(field_num)
            if isinstance(nested, str) and nested.strip():
                strings.append(nested.strip())
        if strings:
            # Prefer the longest readable label (avoids truncated shards).
            return max(strings, key=len)
    # Proto LoadChInfo.ch_name = 4
    direct = block.get(4)
    if isinstance(direct, str) and direct.strip() and _looks_like_name(direct):
        return direct.strip()
    return None


def _collect_strings(node: Any, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(node, str) and node.strip():
        out.append(node.strip())
        return
    if isinstance(node, dict):
        for value in node.values():
            _collect_strings(value, out, depth + 1)


def _looks_like_name(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    return sum(1 for c in stripped if c.isalpha()) >= 2


MAX_CIRCUITS = 40


def _find_float_in_tree(node: dict[int, Any], field_num: int, depth: int = 0) -> float | None:
    if depth > 8:
        return None
    val = node.get(field_num)
    if isinstance(val, list) and val:
        val = val[-1]
    if isinstance(val, bool):
        return 1.0 if val else 0.0
    if isinstance(val, (int, float)):
        return float(val)
    for value in node.values():
        if isinstance(value, dict):
            found = _find_float_in_tree(value, field_num, depth + 1)
            if found is not None:
                return found
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found = _find_float_in_tree(item, field_num, depth + 1)
                    if found is not None:
                        return found
    return None


def _root_float(root: dict[int, Any], field_num: int) -> float | None:
    val = root.get(field_num)
    if isinstance(val, (int, float)):
        return float(val)
    return None


def parse_panel_flat_telemetry(serial_number: str, flat: dict[str, Any]):
    """Parse accumulated flat Ocean Panel MQTT telemetry into EcoflowPanelState."""
    from datetime import datetime, timezone

    from .models import EcoflowPanelState

    circuit_power: dict[int, float] = {}
    circuit_voltage: dict[int, float] = {}
    circuit_active: dict[int, bool] = {}
    circuit_names: dict[int, str] = {}
    circuit_set_amp: dict[int, float] = {}

    for key, value in flat.items():
        if key.startswith("ch") and key.endswith("_power_w"):
            try:
                ch = int(key[2 : key.index("_power_w")])
                circuit_power[ch] = float(value)
            except (ValueError, TypeError):
                continue
        elif key.startswith("ch") and key.endswith("_voltage_v"):
            try:
                ch = int(key[2 : key.index("_voltage_v")])
                circuit_voltage[ch] = float(value)
            except (ValueError, TypeError):
                continue
        elif key.startswith("ch") and key.endswith("_active"):
            try:
                ch = int(key[2 : key.index("_active")])
                circuit_active[ch] = bool(value)
            except (ValueError, TypeError):
                continue
        elif key.startswith("ch") and key.endswith("_name"):
            try:
                ch = int(key[2 : key.index("_name")])
                circuit_names[ch] = str(value)
            except (ValueError, TypeError):
                continue
        elif key.startswith("ch") and key.endswith("_set_amp"):
            try:
                ch = int(key[2 : key.index("_set_amp")])
                circuit_set_amp[ch] = float(value)
            except (ValueError, TypeError):
                continue

    # Prefer clear feed labels over generic "OCEAN Pro" app names on ch38/40.
    for channel, label in INVERTER_FEED_LABELS.items():
        existing = (circuit_names.get(channel) or "").strip()
        if not existing or existing.upper() in {
            "OCEAN PRO",
            "OCEAN",
            "CIRCUIT 38",
            "CIRCUIT 40",
        }:
            if channel in circuit_power or existing:
                circuit_names[channel] = label

    feed_power = _inverter_feed_power_w(circuit_power)
    branch_power = _branch_home_power_w(circuit_power)
    hall = _maybe_float(flat.get("hall_total_power_w"))
    channel_sum = _maybe_float(flat.get("channel_sum_power_w"))
    # User-confirmed: hall_total_power_w (field 967) is the correct whole-home
    # total, matching the app. Fall back to the branch-circuit sum or
    # channel_sum only when hall_total_power_w itself isn't present.
    home_power = _first_finite(hall, branch_power, channel_sum)
    # User-confirmed: hall_total_power_w (967) − channel_sum_power_w (966) is
    # the Smart Panel's own self-consumption (relays/electronics/display) —
    # power hall_total includes that channel_sum's branch-circuit sum doesn't.
    panel_self_consumption = None
    if hall is not None and channel_sum is not None:
        panel_self_consumption = hall - channel_sum

    ev_power = _ev_power_from_circuits(
        circuit_power,
        circuit_names,
        circuit_active,
        flat.get("linked_ev_charger_serial"),
    )

    online = flat.get("online")
    if online is not None:
        online = bool(online == 1 or online is True)

    return EcoflowPanelState(
        serial_number=serial_number,
        grid_voltage_v=_maybe_float(flat.get("grid_voltage_v")),
        grid_voltage_l1_v=_maybe_float(flat.get("grid_voltage_l1_v")),
        grid_voltage_l2_v=_maybe_float(flat.get("grid_voltage_l2_v")),
        home_power_w=home_power,
        inverter_feed_power_w=feed_power,
        grid_import_power_w=_maybe_float(flat.get("grid_import_power_w")),
        master_grid_power_w=_maybe_float(flat.get("master_grid_power_w")),
        channel_sum_power_w=channel_sum,
        hall_total_power_w=hall,
        panel_self_consumption_w=panel_self_consumption,
        backup_reserve_soc=_maybe_float(flat.get("backup_reserve_soc")),
        solar_backup_reserve_soc=_maybe_float(flat.get("solar_backup_reserve_soc")),
        storm_mode=_maybe_int(flat.get("storm_mode")),
        storm_watch=_maybe_bool(flat.get("storm_watch")),
        storm_enabled=_maybe_bool(flat.get("storm_enabled")),
        linked_ev_charger_serial=flat.get("linked_ev_charger_serial"),
        ev_charge_power_w=ev_power,
        online=online,
        circuit_power_w=circuit_power,
        circuit_voltage_v=circuit_voltage,
        circuit_active=circuit_active,
        circuit_names=circuit_names,
        circuit_set_amp=circuit_set_amp,
        updated_at=datetime.now(tz=timezone.utc),
        raw={"merged": flat},
    )


def _inverter_feed_power_w(circuit_power: dict[int, float]) -> float | None:
    values = [circuit_power[ch] for ch in INVERTER_FEED_CIRCUITS if ch in circuit_power]
    if not values:
        return None
    return sum(values)


def _branch_home_power_w(circuit_power: dict[int, float]) -> float | None:
    values = [
        power
        for channel, power in circuit_power.items()
        if channel not in INVERTER_FEED_CIRCUITS
    ]
    if not values and not circuit_power:
        return None
    if not values:
        return 0.0
    # Live captures show branch-circuit loads reported negative (consuming)
    # while the feed breakers (38/40) are positive (sourcing); normalize to a
    # positive "home load" magnitude regardless of which sign convention a
    # given install reports, since abs() is a no-op when values already come
    # through positive.
    return abs(sum(values))


def _first_finite(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _ev_power_from_circuits(
    circuit_power: dict[int, float],
    circuit_names: dict[int, str],
    circuit_active: dict[int, bool],
    linked_ev_serial: str | None = None,
) -> float | None:
    ev_tokens = ("ev", "charger", "charge", "car", "tesla", "pulse", "powerpulse")
    matches = [
        power
        for channel, power in circuit_power.items()
        if channel not in INVERTER_FEED_CIRCUITS
        and any(token in circuit_names.get(channel, "").lower() for token in ev_tokens)
    ]
    if matches:
        return max(abs(p) for p in matches)
    # Do not guess EV power from "largest circuit" — inverter-feed breakers
    # dominate and are not the charger.
    _ = (circuit_active, linked_ev_serial)
    return None


def _maybe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
