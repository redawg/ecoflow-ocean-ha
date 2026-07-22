"""Tests for US Ocean Pro inverter wire-field mapping."""

from __future__ import annotations

import struct

import pytest

from pyecoflowocean.inverter_decoder import parse_ocean_inverter_payload


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _float_field(field: int, value: float) -> bytes:
    return _tag(field, 5) + struct.pack("<f", value)


def _bytes_field(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _string_field(field: int, value: str) -> bytes:
    return _bytes_field(field, value.encode("utf-8"))


def _header(pdata: bytes, *, cmd_func: int, cmd_id: int) -> bytes:
    return (
        _bytes_field(1, pdata)
        + _varint((8 << 3) | 0)
        + _varint(cmd_func)
        + _varint((9 << 3) | 0)
        + _varint(cmd_id)
    )


def test_us_ocean_pro_power_flow_fields() -> None:
    # pdata with string powers + grid + phases + pack SOC
    pdata = b"".join(
        [
            _float_field(1476, 400.0),
            _float_field(1477, 300.0),
            _float_field(1478, 200.0),
            _float_field(1479, 100.0),
            _float_field(1480, 50.0),
            _float_field(1481, 50.0),
            _float_field(1482, 50.0),
            _float_field(1483, 50.0),
            _float_field(1463, 120.0),
            _float_field(1464, 10.0),
            _float_field(1465, 121.0),
            _float_field(1466, 11.0),
            _float_field(1467, -1000.0),
            _float_field(1468, -1100.0),
            _float_field(53, -2100.0),
            _float_field(515, -800.0),  # export
            _bytes_field(1005, _float_field(5, 88.0)),
        ]
    )
    payload = _bytes_field(1, _header(pdata, cmd_func=254, cmd_id=21))

    flat = parse_ocean_inverter_payload(payload)
    assert flat is not None
    # strings 1-4 + string5(=1480..1483 sum 200) = 1200
    assert flat["mpptPwr"] == pytest.approx(1200.0)
    assert flat["sysGridPwr"] == pytest.approx(-800.0)
    # home ≈ solar + grid + battery(0 when unknown & not forced) — battery None, soc 88 < 95
    # so home not inferred without bpPwr
    assert flat["bpSoc"] == pytest.approx(88.0)
    assert flat["pcsAPhase.actPwr"] == pytest.approx(-1000.0)
    assert flat["pcs_act_pwr"] == pytest.approx(-2100.0)
    assert flat["mppt_string_1_power_w"] == pytest.approx(400.0)
    assert flat["mppt_string_5_power_w"] == pytest.approx(200.0)


def test_battery_pack_multi_header() -> None:
    # Field 44 ("power") is intentionally omitted — live CDO captures show it's
    # unreliable (swings from -136kW to +36kW raw across consecutive frames and
    # tracks field 43 by a fixed ~25x constant unrelated to real voltage).
    # Power is instead derived from voltage_v * (field 43 / 10), sign-flipped so
    # positive raw current (BMS discharge convention) shows as negative watts,
    # matching the app's "negative = discharging" convention everywhere else.
    pack_a = b"".join(
        [
            _float_field(1, 1.0),
            _string_field(3, "HR52ZA1AVH720017"),
            _float_field(5, 1.0),
            _float_field(10, 100.0),
            _float_field(11, 99.0),
            _float_field(20, 99.5),
            _float_field(22, 22.0),
            _float_field(43, 100.0),  # 10.0 A discharging
            _float_field(45, 40500.0),
        ]
    )
    pack_b = b"".join(
        [
            _float_field(1, 1.0),
            _string_field(3, "HR52ZA1AVHB50223"),
            _float_field(5, 2.0),
            _float_field(10, 98.0),
            _float_field(11, 98.0),
            _float_field(20, 97.5),
            _float_field(22, 23.0),
            _float_field(43, -50.0),  # 5.0 A charging
            _float_field(45, 40200.0),
        ]
    )
    payload = (
        _bytes_field(1, _header(pack_a, cmd_func=32, cmd_id=177))
        + _bytes_field(1, _header(pack_b, cmd_func=32, cmd_id=177))
    )
    flat = parse_ocean_inverter_payload(payload)
    assert flat is not None
    assert flat["bp_pack_count"] == 2
    assert flat["bp_packs"][0]["current_a"] == pytest.approx(10.0)
    assert flat["bp_packs"][0]["power_w"] == pytest.approx(-405.0)
    assert flat["bp_packs"][1]["current_a"] == pytest.approx(-5.0)
    assert flat["bp_packs"][1]["power_w"] == pytest.approx(201.0)
    assert flat["bpPwr"] == pytest.approx(-204.0)
    assert flat["bpSoc"] == pytest.approx(98.5)
    assert flat["bp_pack_1_sn"] == "HR52ZA1AVH720017"
    assert flat["bp_pack_2_sn"] == "HR52ZA1AVHB50223"
    assert flat["bp_packs"][0]["voltage_v"] == pytest.approx(40.5)
    assert flat["bp_packs"][0]["temp_c"] == pytest.approx(22.0)
    assert "HR52ZA1AVH720017" in flat["bp_pack_sns"]
    assert "HR52ZA1AVHB50223" in flat["bp_pack_sns"]


def test_battery_pack_shared_bus_current_averaged() -> None:
    # Ground-truthed live 2026-07-19: 4 packs each reporting ~-86 A (tight
    # spread) summed to a ~13.8 kW draw against a ~3.2 kW balance-derived
    # true draw. Reproduce that signature with 4 packs at nearly-identical
    # current and assert the combined bpPwr is now the mean, not the sum.
    def _pack(sn: str, current_deciamps: float, voltage_mv: float) -> bytes:
        return b"".join(
            [
                _float_field(1, 1.0),
                _string_field(3, sn),
                _float_field(10, 83.0),
                _float_field(11, 83.0),
                _float_field(20, 99.5),
                _float_field(22, 22.0),
                _float_field(43, current_deciamps),
                _float_field(45, voltage_mv),
            ]
        )

    packs_raw = [
        _pack("HR52ZA1AVHAT0643", -852.0, 39749.0),
        _pack("HR52ZA1AVHB50213", -881.0, 39755.0),
        _pack("HR52ZA1AVH720017", -880.0, 39756.0),
        _pack("HR52ZA1AVHB50223", -864.0, 39756.0),
    ]
    payload = b"".join(
        _bytes_field(1, _header(p, cmd_func=32, cmd_id=177)) for p in packs_raw
    )
    flat = parse_ocean_inverter_payload(payload)
    assert flat is not None
    assert flat["bp_pack_count"] == 4
    individual_sum = sum(p["power_w"] for p in flat["bp_packs"])
    # Each pack alone reports ~3.4-3.5 kW; a naive sum would land near 13.8 kW.
    assert individual_sum == pytest.approx(13822.7, rel=1e-3)
    # The combined reading should be the mean (one pack's share), not the sum.
    assert flat["bpPwr"] == pytest.approx(individual_sum / 4, rel=1e-3)
    assert abs(flat["bpPwr"]) < 4000.0


def test_battery_pack_shared_bus_current_wide_spread_still_averaged() -> None:
    # Second live ground-truth snapshot, 2026-07-19: 4 packs discharging at
    # -25.5 to -46.4 A (spread ~45%, not tightly clustered) summed to a
    # 5.2 kW draw against a ~1.1-1.2 kW balance-derived true draw (~4.5x
    # overcount). Same direction + non-trivial magnitude is what should
    # matter here, not a tight numeric spread.
    def _pack(sn: str, current_deciamps: float, voltage_mv: float) -> bytes:
        return b"".join(
            [
                _float_field(1, 1.0),
                _string_field(3, sn),
                _float_field(10, 83.0),
                _float_field(11, 83.0),
                _float_field(20, 99.5),
                _float_field(22, 22.0),
                _float_field(43, current_deciamps),
                _float_field(45, voltage_mv),
            ]
        )

    packs_raw = [
        _pack("HR52ZA1AVHAT0643", -275.3, 39749.0),
        _pack("HR52ZA1AVHB50213", -314.6, 39755.0),
        _pack("HR52ZA1AVH720017", -254.7, 39756.0),
        _pack("HR52ZA1AVHB50223", -464.0, 39756.0),
    ]
    payload = b"".join(
        _bytes_field(1, _header(p, cmd_func=32, cmd_id=177)) for p in packs_raw
    )
    flat = parse_ocean_inverter_payload(payload)
    assert flat is not None
    individual_sum = sum(p["power_w"] for p in flat["bp_packs"])
    assert individual_sum == pytest.approx(5202.2, rel=1e-3)
    assert flat["bpPwr"] == pytest.approx(individual_sum / 4, rel=1e-3)


def test_home_balance_when_battery_full() -> None:
    pdata = b"".join(
        [
            _float_field(1476, 1000.0),
            _float_field(1477, 0.0),
            _float_field(1478, 0.0),
            _float_field(1479, 0.0),
            _float_field(515, -200.0),
            _bytes_field(1005, _float_field(5, 100.0)),
        ]
    )
    flat = parse_ocean_inverter_payload(_bytes_field(1, _header(pdata, cmd_func=254, cmd_id=21)))
    assert flat is not None
    # The "assume idle at ≥95% SOC" heuristic feeds the home-balance estimate
    # only — it must NOT be written into bpPwr/battery_power_w, or it would
    # stomp a real, still-fresh pack reading merged in from an earlier
    # message that simply didn't repeat pack data this round.
    assert flat["sysLoadPwr"] == pytest.approx(800.0)
    assert "bpPwr" not in flat
    assert "battery_power_w" not in flat


def test_nested_pack_soc_without_serial() -> None:
    """US frames often publish pack SOC nests without HR52 SN strings."""
    pdata = b"".join(
        [
            _bytes_field(1005, _float_field(5, 99.0)),
            _bytes_field(1006, _float_field(5, 98.5)),
            _bytes_field(1007, _float_field(5, 99.2)),
            _bytes_field(1008, _float_field(5, 98.8)),
        ]
    )
    flat = parse_ocean_inverter_payload(_bytes_field(1, _header(pdata, cmd_func=254, cmd_id=21)))
    assert flat is not None
    assert flat["bp_pack_count"] == 4
    assert flat["bpSoc"] == pytest.approx(98.875)
    assert [p["soc"] for p in flat["bp_packs"]] == pytest.approx([99.0, 98.5, 99.2, 98.8])
