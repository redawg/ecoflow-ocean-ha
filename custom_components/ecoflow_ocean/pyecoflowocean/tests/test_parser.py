"""Tests for pyecoflowocean parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyecoflowocean.parser import parse_system_state

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
