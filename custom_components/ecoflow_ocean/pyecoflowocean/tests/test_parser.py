"""Tests for pyecoflowocean parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyecoflowocean.parser import (
    STALE_POWER_FIELD_MAX_AGE_S,
    merge_telemetry,
    parse_flat_telemetry,
    parse_mqtt_payload,
    parse_system_state,
)

FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "powerocean_detail.json"


@pytest.fixture
def sample_response() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_system_state(sample_response: dict) -> None:
    state = parse_system_state("SN_INVERTERBOX01", sample_response)

    assert state.battery_soc == 5
    assert state.home_power_w == 692.0
    assert state.grid_power_w == 692.0
    assert state.solar_power_w == 0.0
    assert state.work_mode == "self_use"
    assert state.backup_soc_limit == 100
    assert state.discharge_soc_limit == 5


OCEAN_PRO_FIXTURE = (
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "ocean_pro_detail.json"
)


def test_parse_ocean_pro_state() -> None:
    response = json.loads(OCEAN_PRO_FIXTURE.read_text(encoding="utf-8"))
    state = parse_system_state("HR51ZA1AVH770253", response)

    assert state.online is True
    assert state.battery_soc == 0
    assert state.home_power_w == 0.0
    assert state.grid_power_w == 0.0
    assert state.solar_power_w == 0.0


def test_parse_mqtt_latest_quotas() -> None:
    payload = json.dumps(
        {
            "operateType": "latestQuotas",
            "data": {
                "online": 1,
                "quotaMap": {
                    "bpSoc": 72,
                    "sysLoadPwr": 454.0,
                    "sysGridPwr": -4.0,
                    "mpptPwr": 0.0,
                    "bpPwr": -458.0,
                    "emsWordMode": "WORKMODE_SELFUSE",
                },
            },
        }
    ).encode()
    flat = parse_mqtt_payload(payload)
    assert flat is not None
    assert flat["bpSoc"] == 72

    state = parse_flat_telemetry("HR51ZA1AVH770253", flat)
    assert state.battery_soc == 72
    assert state.home_power_w == 454.0
    assert state.grid_power_w == -4.0
    assert state.work_mode == "self_use"


def test_merge_telemetry_without_field_ts_never_expires() -> None:
    """Backward-compat: callers that don't pass field_ts keep old readings forever."""
    tel = merge_telemetry({}, {"sysGridPwr": -3000.0, "bpSoc": 80})
    tel = merge_telemetry(tel, {}, now=1_000_000.0)
    assert tel["sysGridPwr"] == -3000.0
    assert tel["bpSoc"] == 80


def test_merge_telemetry_drops_stale_grid_and_battery_power() -> None:
    """A grid/battery reading that stops refreshing must not look "live" forever.

    Regression test: the site grid meter and per-pack battery current each
    ride their own MQTT frame types (and protobuf omits exact-zero fields),
    so a stale "exporting"/"discharging" reading could otherwise linger for
    tens of minutes after the real value changed.
    """
    ts: dict[str, float] = {}
    tel = merge_telemetry(
        {},
        {"sysGridPwr": -3000.0, "bpPwr": -800.0, "bpSoc": 80},
        field_ts=ts,
        now=0.0,
    )
    assert tel["sysGridPwr"] == -3000.0
    assert tel["bpPwr"] == -800.0

    # A later message refreshes unrelated fields (SOC) but not grid/battery.
    just_before_stale = STALE_POWER_FIELD_MAX_AGE_S - 1
    tel = merge_telemetry(tel, {"bpSoc": 81}, field_ts=ts, now=just_before_stale)
    assert tel["sysGridPwr"] == -3000.0, "not stale yet"
    assert tel["bpPwr"] == -800.0, "not stale yet"

    past_stale = STALE_POWER_FIELD_MAX_AGE_S + 1
    tel = merge_telemetry(tel, {"bpSoc": 82}, field_ts=ts, now=past_stale)
    assert "sysGridPwr" not in tel, "old export reading must expire, not linger"
    assert "bpPwr" not in tel, "old discharge reading must expire, not linger"
    assert tel["bpSoc"] == 82, "unrelated fields are unaffected"

    # A fresh grid/battery reading (now charging, not exporting) resets the clock.
    tel = merge_telemetry(
        tel, {"sysGridPwr": 0.0, "bpPwr": 800.0}, field_ts=ts, now=past_stale
    )
    assert tel["sysGridPwr"] == 0.0
    assert tel["bpPwr"] == 800.0
    tel = merge_telemetry(tel, {}, field_ts=ts, now=past_stale + just_before_stale)
    assert tel["sysGridPwr"] == 0.0, "should not have expired again yet"
    assert tel["bpPwr"] == 800.0


def test_parse_mqtt_property_push() -> None:
    payload = json.dumps(
        {
            "cmdFunc": 96,
            "cmdId": 1,
            "params": {
                "bpSoc": 55,
                "sysLoadPwr": 1200.0,
                "sysGridPwr": 300.0,
                "mpptPwr": 900.0,
                "bpPwr": -100.0,
                "pcsAPhase": {"vol": 240.1, "amp": 5.0, "actPwr": 800.0},
            },
        }
    ).encode()
    flat = parse_mqtt_payload(payload)
    assert flat is not None
    state = parse_flat_telemetry("HR51ZA1AVH770253", flat)
    assert state.battery_soc == 55
    assert state.home_power_w == 1200.0
    assert state.phase_a_voltage_v == 240.1
