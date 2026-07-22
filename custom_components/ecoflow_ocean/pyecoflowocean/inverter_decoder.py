"""Decode Ocean Pro / Power Ocean inverter MQTT (wire protobuf).

US OCEAN Pro (product type 88, e.g. HR51…) publishes live telemetry primarily
under ``cmdFunc=254`` (not the EU ``cmdFunc=96`` ENERGY_STREAM envelope).

Empirical field map from CDO live captures (header.pdata → flat ``1.1.*``):

| Field   | Meaning                                      |
|---------|----------------------------------------------|
| 1476-79 | MPPT string 1–4 power (W)                    |
| 1480-83 | MPPT channels rolled into string 5 (W)       |
| 1463/65 | PCS phase A/B voltage (V)                    |
| 1464/66 | PCS phase A/B current (A)                    |
| 1467/68 | PCS phase A/B active power (W)               |
| 53      | PCS total active power (≈ 1467+1468, signed) |
| 21      | PCS total magnitude twin (cmdId 25)          |
| 517     | Tracks Σ solar (mirror of string sum)        |
| 515     | Grid exchange (W); negative = export         |
| 516     | Residual (= 517+515), not battery            |
| 1005-08 | Pack SOC nested (``.5`` = %)                 |
| 262     | Backup / system SOC fallback                 |
| 1553-56 | Inverter temperatures (°C)                   |

Site power flow (matches EcoFlow app)::

    solar ≈ Σ strings
    grid  = field 515   # −export / +import
    home  ≈ solar + grid − battery   # battery: +charge / −discharge; ≈0 when full/idle
"""

from __future__ import annotations

import logging
from typing import Any

from .wire_decoder import decode_protobuf, flatten_tree, iter_headers, unwrap_payload_root

_LOGGER = logging.getLogger(__name__)

# CDO Ocean Pro / EcoFlow app "Solar strings" 1–5:
#   strings 1–4 → fields 1476–1479 (match app watts)
#   string 5    → sum of remaining MPPT channels 1480–1483
#   (1484 reserved if a dedicated 5th channel appears later)
MPPT_STRING_POWER_FIELDS: tuple[int, ...] = (1476, 1477, 1478, 1479)
MPPT_STRING5_COMPONENT_FIELDS: tuple[int, ...] = (1480, 1481, 1482, 1483)
MPPT_STRING5_DIRECT_FIELD = 1484

# Split-phase PCS
_PCS_A_VOL, _PCS_A_AMP, _PCS_A_PWR = 1463, 1464, 1467
_PCS_B_VOL, _PCS_B_AMP, _PCS_B_PWR = 1465, 1466, 1468
_PCS_TOTAL_SIGNED = 53
_PCS_TOTAL_ABS = 21

# Site grid meter on US cmdFunc 254 (negative = export to grid).
_GRID_PWR = 515

# Battery pack snapshot (src=3, cmdFunc=32, cmdId=177) — empirical CDO map.
_BP_CMD_FUNC = 32
_BP_CMD_ID = 177
# pdata fields (NOT classic JTS1 bpSta numbering):
#   3=SN, 5=slot, 10=soc_rounded (twin of 11), 11=soc, 12=flag/power-twin,
#   20=soh, 22=env_temp_c, 29=remain_wh,
#   33/34=cell_temp max/min, 43=current_a, 44=power_w, 45=voltage_mv
#
# Field 11 (and its rounded twin, field 10) tracks the pack's real charge
# level and varies from pack to pack (e.g. 93.9–94.9% across the 4 packs
# while discharging) — this is what matches the % shown in the EcoFlow app.
# Field 20 sits ~99.9% and is nearly identical across all 4 packs regardless
# of load (a bank-shared value, not a per-pack reading), consistent with a
# state-of-health figure rather than live charge — confirmed against the
# app, which shows the pack charge level at field 11's value, not field 20's.
_BP_SN_FIELD = 3
_BP_SLOT_FIELD = 5
_BP_SOC_DISPLAY = 10
_BP_SOC_FIELD = 11
_BP_SOH_FIELD = 20
_BP_ENV_TEMP = 22
_BP_REMAIN_WH = 29
_BP_CELL_TEMP_MAX = 33
_BP_CELL_TEMP_MIN = 34
_BP_CURRENT = 43
_BP_POWER = 44
_BP_VOLTAGE_MV = 45
_NESTED_PACK_FIELDS = (1005, 1006, 1007, 1008)

# SOC candidates — pack nests first; avoid ambiguous counters (e.g. 1472=string count).
_PACK_SOC_FIELDS = (1005, 1006, 1007, 1008)
_SYSTEM_SOC_FIELDS = (262, 1448)  # system / backup SOC style fields (0–100)
# Operating mode (Self-powered / Intelligent / …) — confirmed via live toggles.
_WORK_MODE_FIELD = 1470


def parse_ocean_inverter_payload(payload: bytes) -> dict[str, Any] | None:
    """Extract flat inverter telemetry including MPPT + power-flow fields."""
    try:
        tree = decode_protobuf(payload)
    except ValueError:
        return None

    flat: dict[str, Any] = {}
    root = unwrap_payload_root(tree)
    headers = iter_headers(tree) or iter_headers(root)
    pdata = _first_pdata(headers) or (
        root.get(1) if isinstance(root.get(1), dict) else root
    )
    if not isinstance(pdata, dict):
        pdata = root

    leaf_vals = _leaf_float_map(tree)

    # --- Battery packs (cmdFunc=32 / nested 1005–1008) ---
    energy_blocks = [
        h.get(1)
        for h in headers
        if isinstance(h.get(1), dict) and (_header_cmd(h)[0] in (None, 254) or 1005 in h.get(1))
    ]
    if isinstance(pdata, dict) and pdata not in energy_blocks:
        energy_blocks.append(pdata)
    bp_flat = _extract_battery_packs(headers, energy_blocks)
    if bp_flat:
        flat.update(bp_flat)

    # --- MPPT strings ---
    strings = _extract_mppt_heartbeat(pdata)
    if not strings:
        strings = _extract_mppt_heartbeat(root)
    if not strings:
        strings = _extract_flat_string_powers(leaf_vals)

    if strings:
        while len(strings) < 5:
            strings.append({"power_w": 0.0, "active": False})
        for idx, entry in enumerate(strings[:5], start=1):
            if entry.get("power_w") is not None:
                flat[f"mppt_string_{idx}_power_w"] = float(entry["power_w"])
            if entry.get("voltage_v") is not None:
                flat[f"mppt_string_{idx}_voltage_v"] = float(entry["voltage_v"])
            if entry.get("current_a") is not None:
                flat[f"mppt_string_{idx}_current_a"] = float(entry["current_a"])
            active = entry.get("active")
            if active is None and entry.get("power_w") is not None:
                active = abs(float(entry["power_w"])) > 5
            if active is not None:
                flat[f"mppt_string_{idx}_active"] = bool(active)
        powers = [float(e["power_w"]) for e in strings[:5] if e.get("power_w") is not None]
        if powers:
            flat["mpptPwr"] = sum(max(p, 0.0) for p in powers)
            flat["solar_power_w"] = flat["mpptPwr"]

    # --- PCS phases ---
    _map_phase(flat, leaf_vals, "pcsAPhase", _PCS_A_VOL, _PCS_A_AMP, _PCS_A_PWR)
    _map_phase(flat, leaf_vals, "pcsBPhase", _PCS_B_VOL, _PCS_B_AMP, _PCS_B_PWR)

    phase_sum = None
    a = leaf_vals.get(_PCS_A_PWR)
    b = leaf_vals.get(_PCS_B_PWR)
    if a is not None and b is not None:
        phase_sum = a + b
        flat["pcs_act_pwr"] = phase_sum
    elif _PCS_TOTAL_SIGNED in leaf_vals:
        phase_sum = leaf_vals[_PCS_TOTAL_SIGNED]
        flat["pcs_act_pwr"] = phase_sum
    elif _PCS_TOTAL_ABS in leaf_vals and abs(leaf_vals[_PCS_TOTAL_ABS]) > 50:
        # cmdId 25 often carries the positive twin only
        flat["pcs_act_pwr"] = leaf_vals[_PCS_TOTAL_ABS]

    # --- Grid (site PCS meter) ---
    grid = leaf_vals.get(_GRID_PWR)
    if grid is not None and abs(grid) < 50000:
        # Keep EcoFlow sign: positive import, negative export.
        flat["sysGridPwr"] = float(grid)
        flat["grid_power_w"] = float(grid)

    # --- SOC (prefer pack reports already applied above) ---
    if flat.get("bpSoc") is None:
        soc = _extract_soc(tree, leaf_vals)
        if soc is not None:
            flat["bpSoc"] = soc
            flat["battery_soc"] = soc

    # --- Home from balance: home ≈ solar + grid − battery ---
    # (battery negative = discharging, positive = charging; subtracting adds
    # discharge power into the home estimate, matching the UI's sign convention.)
    solar = flat.get("mpptPwr")
    if solar is not None and flat.get("sysGridPwr") is not None:
        battery = flat.get("bpPwr")
        soc = flat.get("bpSoc")
        # When SOC is full/nearly full and this message carries no pack data,
        # assume idle *for this home-estimate only* — this is a frequent,
        # cheap heartbeat frame that simply didn't include pack current this
        # round, not proof the battery is actually idle. Do NOT write this
        # guess into bpPwr/battery_power_w: doing so used to stomp a real,
        # still-fresh pack reading already sitting in the merged telemetry
        # cache from a moment earlier with a false zero.
        home_battery_estimate = battery
        if home_battery_estimate is None and soc is not None and float(soc) >= 95:
            home_battery_estimate = 0.0
        if home_battery_estimate is not None:
            home = float(solar) + float(flat["sysGridPwr"]) - float(home_battery_estimate)
            if 0 <= home < 50000:
                flat["sysLoadPwr"] = home
                flat["home_power_w"] = home

    # --- Temps (informational) ---
    for idx, field_num in enumerate((1553, 1554, 1555, 1556), start=1):
        temp = leaf_vals.get(field_num)
        if temp is not None and 0 < temp < 120:
            flat[f"inverter_temp_{idx}_c"] = float(temp)

    # --- EMS operating mode (Self-powered / Intelligent / …) ---
    mode_raw = leaf_vals.get(_WORK_MODE_FIELD)
    if mode_raw is None:
        # Also accept nested path finds (same helper style as panel).
        from .panel_decoder import _find_float_in_tree

        mode_raw = _find_float_in_tree(tree, _WORK_MODE_FIELD)
    if mode_raw is not None and mode_raw >= 0:
        mode_code = int(mode_raw)
        flat["ems_work_mode_code"] = mode_code
        from .const import EMS_WORK_MODE_CODES

        label = EMS_WORK_MODE_CODES.get(mode_code)
        if label:
            # Feed the existing MQTT→state path that reads emsWordMode.
            flat["emsWordMode"] = f"WORKMODE_{label.upper()}"
            flat["work_mode"] = label

    if not flat:
        return None

    _LOGGER.debug(
        "Ocean inverter decoded: solar=%s home=%s grid=%s bp=%s soc=%s pcs=%s",
        flat.get("mpptPwr"),
        flat.get("sysLoadPwr"),
        flat.get("sysGridPwr"),
        flat.get("bpPwr"),
        flat.get("bpSoc"),
        flat.get("pcs_act_pwr"),
    )
    return flat


def _first_pdata(headers: list[dict[int, Any]]) -> dict[int, Any] | None:
    for header in headers:
        pdata = header.get(1)
        if isinstance(pdata, dict):
            return pdata
        if isinstance(pdata, list):
            for item in pdata:
                if isinstance(item, dict):
                    return item
    return None


def _header_cmd(header: dict[int, Any]) -> tuple[int | None, int | None]:
    cmd_func = header.get(8)
    cmd_id = header.get(9)
    if isinstance(cmd_func, list):
        cmd_func = cmd_func[-1] if cmd_func else None
    if isinstance(cmd_id, list):
        cmd_id = cmd_id[-1] if cmd_id else None
    try:
        return (
            int(cmd_func) if cmd_func is not None else None,
            int(cmd_id) if cmd_id is not None else None,
        )
    except (TypeError, ValueError):
        return None, None


def _extract_battery_packs(
    headers: list[dict[int, Any]],
    energy_pdatas: list[dict[int, Any]] | dict[int, Any] | None = None,
) -> dict[str, Any]:
    """Decode per-pack battery details; dedupe by serial number."""
    packs_by_sn: dict[str, dict[str, Any]] = {}

    # Rich snapshots from cmdFunc=32 / cmdId=177
    for header in headers:
        cmd_func, cmd_id = _header_cmd(header)
        if cmd_func != _BP_CMD_FUNC:
            continue
        if cmd_id is not None and cmd_id != _BP_CMD_ID:
            continue
        pdata = header.get(1)
        for pack in pdata if isinstance(pdata, list) else [pdata]:
            detail = _pack_from_cmd32(pack)
            if detail and detail.get("sn"):
                packs_by_sn[str(detail["sn"])] = detail

    # Lightweight SN+SOC from energy-stream nests (1005–1008)
    blocks: list[Any]
    if isinstance(energy_pdatas, dict):
        blocks = [energy_pdatas]
    elif isinstance(energy_pdatas, list):
        blocks = energy_pdatas
    else:
        blocks = []
    for energy_pdata in blocks:
        if not isinstance(energy_pdata, dict):
            continue
        for field_num in _NESTED_PACK_FIELDS:
            block = energy_pdata.get(field_num)
            detail = _pack_from_nested(block, field_num)
            if not detail or not detail.get("sn"):
                continue
            sn = str(detail["sn"])
            if sn in packs_by_sn:
                packs_by_sn[sn] = {**detail, **packs_by_sn[sn]}
            else:
                packs_by_sn[sn] = detail

    # Fallback: SOC-only nests (field 1005–1008 .5) without serial strings.
    if not packs_by_sn:
        for pack in _packs_from_soc_paths(headers, blocks):
            key = str(pack.get("sn") or f"slot-{pack.get('slot')}")
            packs_by_sn[key] = pack

    if not packs_by_sn:
        return {}

    packs = sorted(
        packs_by_sn.values(),
        key=lambda p: (p.get("slot") is None, p.get("slot") or 0, str(p.get("sn") or "")),
    )
    for idx, pack in enumerate(packs, start=1):
        pack["index"] = idx

    flat: dict[str, Any] = {"bp_packs": packs, "bp_pack_count": len(packs)}
    socs = [float(p["soc"]) for p in packs if p.get("soc") is not None]
    pwrs = [float(p["power_w"]) for p in packs if p.get("power_w") is not None]
    if socs:
        flat["bpSoc"] = sum(socs) / len(socs)
        flat["battery_soc"] = flat["bpSoc"]
    if pwrs:
        flat["bpPwr"] = _combine_pack_power(packs, pwrs)
        flat["battery_power_w"] = flat["bpPwr"]
    flat["bp_pack_sns"] = [str(p["sn"]) for p in packs if p.get("sn")]

    for pack in packs:
        idx = pack["index"]
        if pack.get("soc") is not None:
            flat[f"bp_pack_{idx}_soc"] = pack["soc"]
        if pack.get("power_w") is not None:
            flat[f"bp_pack_{idx}_power_w"] = pack["power_w"]
        if pack.get("soh") is not None:
            flat[f"bp_pack_{idx}_soh"] = pack["soh"]
        if pack.get("temp_c") is not None:
            flat[f"bp_pack_{idx}_temp_c"] = pack["temp_c"]
        if pack.get("sn"):
            flat[f"bp_pack_{idx}_sn"] = pack["sn"]
    return flat


_SHARED_BUS_CURRENT_FLOOR_A = 5.0


def _combine_pack_power(packs: list[dict[str, Any]], pwrs: list[float]) -> float:
    """Combine per-pack power into one system-wide battery reading.

    Parallel packs on this hardware routinely each report *close to* the
    same shared bus current instead of their own individual branch share —
    the BMS appears to mirror one shunt reading (with per-pack sampling
    jitter) into every pack's status blob. Summing N such readings then
    inflates the true total by roughly Nx. Two live, ground-truthed
    snapshots on 2026-07-19 confirm this isn't a rare edge case:

      - 4 packs at ≈-86 A each (spread <3%) summed to a 13.8 kW battery
        draw vs. a solar/feed-balance-derived true draw of ≈3.2 kW
        (a 4.3x overcount); averaging landed within ~7% instead.
      - 4 packs at -25.5 to -46.4 A (spread ≈45%, still all discharging)
        summed to a 5.2 kW draw vs. a true draw of ≈1.1-1.2 kW (a ~4.5x
        overcount); averaging landed within ~13% instead.

    So the trigger for averaging isn't "nearly identical", it's "multiple
    packs simultaneously drawing non-trivial current in the *same*
    direction" — that's the shared-bus-echo signature on this hardware.
    Genuinely independent packs (e.g. one discharging while another
    charges, as modeled in test_battery_pack_multi_header) keep the plain
    sum, since averaging those would understate real, opposing per-pack
    contributions.
    """
    if len(pwrs) <= 1:
        return pwrs[0] if pwrs else 0.0

    currents = [float(p["current_a"]) for p in packs if p.get("current_a") is not None]
    if len(currents) == len(pwrs):
        significant = [c for c in currents if abs(c) >= _SHARED_BUS_CURRENT_FLOOR_A]
        if len(significant) >= 2 and len({c > 0 for c in significant}) == 1:
            return sum(pwrs) / len(pwrs)

    return sum(pwrs)


def _looks_like_pack_sn(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 8:
        return False
    # Ocean Pro packs are typically HR52…; accept other EcoFlow HR5* serials too.
    return value.startswith("HR5")


def _pack_from_cmd32(pack: Any) -> dict[str, Any] | None:
    if not isinstance(pack, dict):
        return None
    sn = pack.get(_BP_SN_FIELD)
    if not _looks_like_pack_sn(sn):
        sn = next(
            (v for v in pack.values() if _looks_like_pack_sn(v)),
            None,
        )
    if not sn:
        return None

    detail: dict[str, Any] = {"sn": sn}
    slot = pack.get(_BP_SLOT_FIELD)
    if isinstance(slot, (int, float)) and 1 <= int(slot) <= 16:
        detail["slot"] = int(slot)

    soc = pack.get(_BP_SOC_FIELD)
    display_soc = pack.get(_BP_SOC_DISPLAY)
    if isinstance(soc, (int, float)) and 0 <= float(soc) <= 100:
        detail["soc"] = float(soc)
    elif isinstance(display_soc, (int, float)) and 0 <= float(display_soc) <= 100:
        detail["soc"] = float(display_soc)

    soh = pack.get(_BP_SOH_FIELD)
    if isinstance(soh, (int, float)) and 0 <= float(soh) <= 100:
        detail["soh"] = float(soh)

    voltage_mv = pack.get(_BP_VOLTAGE_MV)
    if isinstance(voltage_mv, (int, float)):
        mv = float(voltage_mv)
        if 1000 <= mv <= 100000:
            detail["voltage_v"] = mv / 1000.0
        elif 10 <= mv <= 100:
            detail["voltage_v"] = mv

    # Field 44 ("power") is unreliable on CDO live frames — its magnitude swings
    # wildly (raw values from -136k to +36k across consecutive messages, i.e.
    # tens of kW either way) and correlates almost exactly with field 43 scaled
    # by a fixed ~25x constant unrelated to the pack's real voltage, so it looks
    # like a stale/derived debug value rather than true instantaneous power.
    # Field 43 ("current") in deci-amps combined with the real pack voltage
    # gives a self-consistent, physically sane per-pack wattage instead.
    current = pack.get(_BP_CURRENT)
    if isinstance(current, (int, float)) and abs(float(current)) < 3000:
        current_a = float(current) / 10.0
        detail["current_a"] = current_a
        voltage_v = detail.get("voltage_v")
        if voltage_v is not None:
            # BMS convention: positive current = discharging. Flip sign so
            # power_w matches the rest of the app (negative = discharging,
            # positive = charging).
            detail["power_w"] = -voltage_v * current_a

    for key, field in (
        ("temp_c", _BP_ENV_TEMP),
        ("cell_temp_max_c", _BP_CELL_TEMP_MAX),
        ("cell_temp_min_c", _BP_CELL_TEMP_MIN),
    ):
        val = pack.get(field)
        if isinstance(val, (int, float)) and -20 <= float(val) <= 90:
            detail[key] = float(val)

    remain = pack.get(_BP_REMAIN_WH)
    if isinstance(remain, (int, float)) and 0 <= float(remain) < 500000:
        detail["remain_wh"] = float(remain)

    return detail


def _pack_from_nested(block: Any, field_num: int) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    slot = field_num - 1004
    sn = block.get(3)
    if _looks_like_pack_sn(sn):
        detail: dict[str, Any] = {"sn": str(sn), "slot": slot}
    else:
        # Live US frames often publish SOC without the SN string on this nest.
        detail = {"sn": f"P{slot}", "slot": slot}
    soc = block.get(5)
    if isinstance(soc, (int, float)) and 0 <= float(soc) <= 100:
        detail["soc"] = float(soc)
    # Require at least SOC (or a real SN) so empty nests are ignored.
    if detail.get("soc") is None and not _looks_like_pack_sn(sn):
        return None
    return detail


def _packs_from_soc_paths(
    headers: list[dict[int, Any]],
    energy_pdatas: list[Any],
) -> list[dict[str, Any]]:
    """Build packs from flattened ``*.1005.5`` SOC paths when nest dicts lack SN."""
    trees: list[Any] = []
    for header in headers:
        trees.append(header)
        pdata = header.get(1)
        if pdata is not None:
            trees.append(pdata)
    trees.extend(energy_pdatas)

    found: dict[int, dict[str, Any]] = {}
    for tree in trees:
        if not isinstance(tree, dict):
            continue
        try:
            flat_paths = flatten_tree(tree)
        except Exception:
            continue
        for path, val in flat_paths.items():
            if not isinstance(val, (int, float)):
                continue
            parts = str(path).split(".")
            if len(parts) < 2 or parts[-1] != "5":
                continue
            try:
                parent = int(parts[-2].split("[", 1)[0])
            except ValueError:
                continue
            if parent not in _PACK_SOC_FIELDS:
                continue
            if not 0 <= float(val) <= 100:
                continue
            slot = parent - 1004
            found[slot] = {
                "sn": f"P{slot}",
                "slot": slot,
                "soc": float(val),
            }
    return [found[k] for k in sorted(found)]


def _path_field_num(path: str) -> int | None:
    """Last path segment as field number, stripping repeated-index suffixes."""
    segment = str(path).rsplit(".", 1)[-1]
    if "[" in segment:
        segment = segment.split("[", 1)[0]
    try:
        return int(segment)
    except ValueError:
        return None


def _leaf_float_map(tree: dict[int, Any]) -> dict[int, float]:
    """Collect last-segment field_num → float, preferring pdata (``1.1.*``)."""
    weak: dict[int, float] = {}
    strong: dict[int, float] = {}
    for path, val in flatten_tree(tree).items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        fval = float(val)
        if abs(fval) >= 1e7:
            continue
        field = _path_field_num(str(path))
        if field is None:
            continue
        path_s = str(path)
        if path_s.startswith("1.1.") or ".1." in path_s or path_s.startswith("1["):
            strong[field] = fval
        else:
            weak[field] = fval
    out = dict(weak)
    out.update(strong)
    return out


def _map_phase(
    flat: dict[str, Any],
    leaf_vals: dict[int, float],
    prefix: str,
    vol_f: int,
    amp_f: int,
    pwr_f: int,
) -> None:
    vol = leaf_vals.get(vol_f)
    amp = leaf_vals.get(amp_f)
    pwr = leaf_vals.get(pwr_f)
    phase: dict[str, float] = {}
    if vol is not None and 80 <= vol <= 280:
        phase["vol"] = vol
        flat[f"{prefix}.vol"] = vol
    if amp is not None and abs(amp) < 500:
        phase["amp"] = amp
        flat[f"{prefix}.amp"] = amp
    if pwr is not None and abs(pwr) < 50000:
        phase["actPwr"] = pwr
        flat[f"{prefix}.actPwr"] = pwr
    if phase:
        flat[prefix] = phase


def _extract_soc(tree: dict[int, Any], leaf_vals: dict[int, float]) -> float | None:
    """Prefer nested pack SOC (1005–1008), then system / BP-report clusters."""
    pack_socs: list[float] = []
    for path, val in flatten_tree(tree).items():
        if not isinstance(val, (int, float)):
            continue
        # Paths like 1.1.1005.5
        parts = str(path).split(".")
        if len(parts) >= 2 and parts[-1] == "5":
            try:
                parent = int(parts[-2])
            except ValueError:
                continue
            if parent in _PACK_SOC_FIELDS and 0 <= float(val) <= 100:
                pack_socs.append(float(val))
    if pack_socs:
        return sum(pack_socs) / len(pack_socs)

    for field_num in _SYSTEM_SOC_FIELDS:
        val = leaf_vals.get(field_num)
        if val is not None and 0 <= val <= 100:
            return float(val)

    # cmdFunc 32-style BP snapshot: several of fields 10–21 clustered in 0–100.
    cluster = [
        leaf_vals[f]
        for f in range(10, 22)
        if f in leaf_vals and 5.0 <= leaf_vals[f] <= 100.0
    ]
    if len(cluster) >= 4:
        cluster.sort()
        mid = cluster[len(cluster) // 2]
        near = [v for v in cluster if abs(v - mid) <= 2.0]
        if len(near) >= 4:
            return sum(near) / len(near)
    return None


def _extract_flat_string_powers(leaf_vals: dict[int, float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    any_present = False
    for field_num in MPPT_STRING_POWER_FIELDS:
        power = leaf_vals.get(field_num)
        if power is None:
            out.append({"power_w": 0.0, "active": False})
            continue
        any_present = True
        out.append({"power_w": float(power), "active": abs(power) > 5})

    # String 5: prefer dedicated 1484; else sum secondary MPPT channels.
    direct = leaf_vals.get(MPPT_STRING5_DIRECT_FIELD)
    if direct is not None and abs(direct) > 5:
        any_present = True
        out.append({"power_w": float(direct), "active": True})
    else:
        components = [leaf_vals[f] for f in MPPT_STRING5_COMPONENT_FIELDS if f in leaf_vals]
        if components:
            any_present = True
            power5 = sum(max(float(v), 0.0) for v in components)
            out.append({"power_w": power5, "active": power5 > 5})
        else:
            out.append({"power_w": 0.0, "active": False})

    return out if any_present else []


def _extract_mppt_heartbeat(pdata: dict[int, Any]) -> list[dict[str, Any]]:
    """Parse proto mpptHeartBeat (field 31) → list of PV string dicts."""
    block = pdata.get(31)
    entries: list[dict[str, Any]] = []
    if isinstance(block, dict):
        entries.extend(_mppt_pv_list(block))
    elif isinstance(block, list):
        for item in block:
            if isinstance(item, dict):
                entries.extend(_mppt_pv_list(item))
    if not entries:
        for value in pdata.values():
            if isinstance(value, dict) and 31 in value:
                nested = value.get(31)
                if isinstance(nested, dict):
                    entries.extend(_mppt_pv_list(nested))
    return entries[:8]


def _mppt_pv_list(heartbeat: dict[int, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pv = heartbeat.get(1)
    if isinstance(pv, list):
        candidates: list[Any] = pv
    elif isinstance(pv, dict):
        if {1, 2, 3} & set(pv.keys()) and all(
            not isinstance(pv.get(k), dict) for k in (1, 2, 3)
        ):
            candidates = [pv]
        else:
            candidates = [v for _, v in sorted(pv.items()) if isinstance(v, dict)]
    else:
        return out

    for item in candidates:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        if isinstance(item.get(1), (int, float)):
            entry["voltage_v"] = float(item[1])
        if isinstance(item.get(2), (int, float)):
            entry["current_a"] = float(item[2])
        if isinstance(item.get(3), (int, float)):
            entry["power_w"] = float(item[3])
        sta = item.get(4)
        if isinstance(sta, bool):
            entry["active"] = sta
        elif isinstance(sta, (int, float)):
            entry["active"] = float(sta) >= 0.5
        elif "power_w" in entry:
            entry["active"] = abs(entry["power_w"]) > 5
        if entry:
            out.append(entry)
    return out
