"""Tests for EV charger protobuf decoder."""

from __future__ import annotations

from pyecoflowocean.ev_charger_decoder import (
    parse_ev_charger_flat_telemetry,
    parse_ev_charger_payload,
)


def test_parse_ev_charger_session_block() -> None:
    flat = {
        "charge_power_w": 1440.0,
        "output_voltage_v": 96.0,
        "vehicle_connected": True,
        "charging_active": True,
        "max_current_a": 60.0,
        "charge_limit_percent": 100.0,
    }
    state = parse_ev_charger_flat_telemetry("C102ZA1AZH6G0018", flat)
    assert state.charge_power_w == 1440.0
    assert state.output_voltage_v == 96.0
    assert state.vehicle_connected is True
    assert state.charging_active is True
    assert state.max_current_a == 60.0


def test_parse_ev_charger_payload_empty() -> None:
    assert parse_ev_charger_payload(b"") is None
