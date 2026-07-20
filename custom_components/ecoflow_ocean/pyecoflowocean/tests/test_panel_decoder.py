"""Tests for Ocean Panel protobuf decoder."""

from __future__ import annotations

from pyecoflowocean.panel_decoder import parse_ocean_panel_payload, parse_panel_flat_telemetry

# Captured live from HR61ZA1AVH7X0100 (401 bytes)
SAMPLE_PAYLOAD_HEX = (
    "0a96020aba010a60ad03181de2c59d2033f7d9c5a52070600944ad204123eb45bd5bcc9ef642c55b"
    "0fa7ec41cd5bc8edf542d55bc3d7ee41dd5b53435ec5e55b961764c5a55c4f4e0845ad5c93867b44b"
    "55cb267c744bd5cd94bcb44d55ca61ee743dd5c1ebe20441060182020012801380340fe01481550605801"
)


def test_parse_panel_flat_telemetry_extended_fields() -> None:
    flat = {
        "backup_reserve_soc": 100,
        "solar_backup_reserve_soc": 90,
        "storm_enabled": True,
        "linked_ev_charger_serial": "C102ZA1AZH6G0018",
        "hall_total_power_w": 1200.0,
        "ch12_power_w": 410.0,
        "ch12_name": "Garage EV",
        "ch38_power_w": 2800.0,
        "ch40_power_w": 2900.0,
        "grid_import_power_w": -245.0,
    }
    state = parse_panel_flat_telemetry("HR61ZA1AVH7X0100", flat)
    assert state.backup_reserve_soc == 100
    assert state.solar_backup_reserve_soc == 90
    assert state.storm_enabled is True
    assert state.linked_ev_charger_serial == "C102ZA1AZH6G0018"
    assert state.ev_charge_power_w == 410.0
    assert state.circuit_names[12] == "Garage EV"
    assert state.circuit_names[38] == "Inverter feed L1"
    assert state.circuit_names[40] == "Inverter feed L2"
    assert state.inverter_feed_power_w == 5700.0
    assert state.home_power_w == 1200.0  # prefers hall_total_power_w (confirmed against the app)
    assert state.grid_import_power_w == -245.0


def test_inverter_feed_excluded_from_branch_home() -> None:
    flat = {
        "ch4_power_w": 100.0,
        "ch38_power_w": 2500.0,
        "ch40_power_w": 2600.0,
    }
    state = parse_panel_flat_telemetry("HR61ZA1AVH7X0100", flat)
    assert state.inverter_feed_power_w == 5100.0
    assert state.home_power_w == 100.0


def test_parse_ocean_panel_live_sample() -> None:
    # Multi-circuit payload captured live from HR61ZA1AVH7X0100
    payload = bytes.fromhex(
        "0abb010a60ad03fc72e1c59d209a6dd7c5a520e84c1a44ad2037b7ea45bd5b4f3ff642c55b"
        "badcec41cd5b094cf642d55b40ddf341dd5b7a775ec5e55b1b0a65c5a55c7b5c0845ad5c23117c44b"
        "55c410bc844bd5c1baacb44d55c6f3ce643dd5cea4d1b441060182020012801380340fe01481550605801"
    )
    flat = parse_ocean_panel_payload(payload)
    assert flat is not None
    assert any(k.endswith("_power_w") for k in flat)

    state = parse_panel_flat_telemetry("HR61ZA1AVH7X0100", flat)
    assert state.circuit_power_w
    assert state.home_power_w is not None
