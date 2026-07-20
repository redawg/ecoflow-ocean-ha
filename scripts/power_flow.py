#!/usr/bin/env python3
"""Show live EcoFlow Power Ocean power flow in the terminal.

Usage:
  python scripts/power_flow.py              # EcoFlow API (needs ECOFLOW_* env vars)
  python scripts/power_flow.py --ha         # Home Assistant REST API (needs HA_TOKEN)
  python scripts/power_flow.py --watch 5    # Refresh every 5 seconds

Environment variables:
  ECOFLOW_EMAIL, ECOFLOW_PASSWORD, ECOFLOW_SERIAL, ECOFLOW_PRODUCT_TYPE=88
  HA_URL (default http://172.16.255.250:8123), HA_TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.const import PRODUCT_TYPE_POWER_OCEAN
from pyecoflowocean.models import EcoflowOceanState


@dataclass
class PowerFlow:
    solar_w: float
    grid_w: float
    battery_w: float
    home_w: float
    soc: float | None
    online: bool | None
    work_mode: str | None
    updated_at: str | None
    serial: str

    @classmethod
    def from_state(cls, state: EcoflowOceanState) -> PowerFlow:
        return cls(
            solar_w=state.solar_power_w or 0.0,
            grid_w=state.grid_power_w or 0.0,
            battery_w=state.battery_power_w or 0.0,
            home_w=state.home_power_w or 0.0,
            soc=state.battery_soc,
            online=state.online,
            work_mode=state.work_mode,
            updated_at=state.updated_at.isoformat() if state.updated_at else None,
            serial=state.serial_number,
        )


def _fmt_w(w: float) -> str:
    if abs(w) >= 1000:
        return f"{w / 1000:.2f} kW"
    return f"{w:.0f} W"


def _arrow(w: float, label: str) -> str:
    if abs(w) < 10:
        return f"  {label}: idle"
    direction = "→" if w > 0 else "←"
    return f"  {label}: {direction} {_fmt_w(abs(w))}"


def render(flow: PowerFlow) -> str:
    grid_label = "importing" if flow.grid_w > 10 else "exporting" if flow.grid_w < -10 else "idle"
    batt_label = "charging" if flow.battery_w > 10 else "discharging" if flow.battery_w < -10 else "idle"
    online = "online" if flow.online else "offline" if flow.online is False else "unknown"
    soc = f"{flow.soc:.0f}%" if flow.soc is not None else "—"

    lines = [
        "",
        "╔══════════════════════════════════════════════════════════╗",
        "║           EcoFlow Power Ocean — Live Power Flow          ║",
        "╚══════════════════════════════════════════════════════════╝",
        f"  Serial: {flow.serial}   Status: {online}   SOC: {soc}",
        f"  Mode: {flow.work_mode or '—'}   Updated: {flow.updated_at or '—'}",
        "",
        "                    ┌─────────────┐",
        f"                    │   SOLAR     │",
        f"                    │  {_fmt_w(flow.solar_w):>10}  │",
        "                    └──────┬──────┘",
        "                           │",
        "              ┌────────────┼────────────┐",
        "              ▼            ▼            ▼",
        "       ┌──────────┐  ┌──────────┐  ┌──────────┐",
        f"       │  BATTERY │  │   HOME   │  │   GRID   │",
        f"       │ {_fmt_w(flow.battery_w):>8} │  │ {_fmt_w(flow.home_w):>8} │  │ {_fmt_w(flow.grid_w):>8} │",
        f"       │ {batt_label:^8} │  │  load    │  │ {grid_label:^8} │",
        "       └──────────┘  └──────────┘  └──────────┘",
        "",
        "  Flow detail:",
        _arrow(flow.solar_w, "Solar production"),
        _arrow(flow.home_w, "Home consumption"),
        _arrow(flow.battery_w, "Battery (+ charge / − discharge)"),
        _arrow(flow.grid_w, "Grid (+ import / − export)"),
        "",
        f"  Balance check: solar {_fmt_w(flow.solar_w)} ≈ home {_fmt_w(flow.home_w)}"
        f" + batt {_fmt_w(flow.battery_w)} + grid {_fmt_w(-flow.grid_w)}",
        "",
    ]
    return "\n".join(lines)


async def fetch_ecoflow() -> PowerFlow:
    email = os.environ["ECOFLOW_EMAIL"]
    password = os.environ["ECOFLOW_PASSWORD"]
    serial = os.environ.get("ECOFLOW_SERIAL", "HR51ZA1AVH770253")
    product_type = os.environ.get("ECOFLOW_PRODUCT_TYPE", PRODUCT_TYPE_POWER_OCEAN)

    api = EcoflowOcean(
        email,
        password,
        serial_number=serial,
        product_type=product_type,
    )
    try:
        await api.login()
        state = await api.get_system_state(serial, product_type=product_type)
        return PowerFlow.from_state(state)
    finally:
        await api.close()


def fetch_ha() -> PowerFlow:
    base = os.environ.get("HA_URL", "http://172.16.255.250:8123")
    token = os.environ["HA_TOKEN"]
    serial = os.environ.get("ECOFLOW_SERIAL", "")

    req = urllib.request.Request(
        f"{base}/api/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        states = json.loads(resp.read())

    def get(suffix: str) -> float | str | None:
        for state in states:
            eid = state["entity_id"]
            if not eid.startswith("sensor.") and not eid.startswith("binary_sensor."):
                continue
            if serial and serial not in eid:
                continue
            if eid.endswith(suffix):
                val = state.get("state")
                if val in (None, "unknown", "unavailable"):
                    return None
                if suffix == "_online":
                    return val
                if suffix == "_work_mode":
                    return val
                if suffix == "_last_updated":
                    return val
                try:
                    return float(val)
                except ValueError:
                    return val
        return None

    return PowerFlow(
        solar_w=float(get("_solar_power") or 0),
        grid_w=float(get("_grid_power") or 0),
        battery_w=float(get("_battery_power") or 0),
        home_w=float(get("_home_power") or 0),
        soc=float(get("_battery_soc")) if get("_battery_soc") is not None else None,
        online=get("_online") == "on" if get("_online") is not None else None,
        work_mode=str(get("_work_mode")) if get("_work_mode") else None,
        updated_at=str(get("_last_updated")) if get("_last_updated") else None,
        serial=serial or "from HA",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Show EcoFlow live power flow")
    parser.add_argument("--ha", action="store_true", help="Fetch from Home Assistant")
    parser.add_argument("--watch", type=float, metavar="SEC", help="Refresh interval")
    args = parser.parse_args()

    def show() -> None:
        if args.ha:
            flow = fetch_ha()
        else:
            flow = asyncio.run(fetch_ecoflow())
        print(render(flow))

    try:
        if args.watch:
            while True:
                if args.watch and sys.stdout.isatty():
                    print("\033[2J\033[H", end="")
                show()
                time.sleep(args.watch)
        else:
            show()
    except KeyError as err:
        missing = str(err).strip("'")
        print(f"Missing environment variable: {missing}", file=sys.stderr)
        print("\nEcoFlow API:", file=sys.stderr)
        print("  ECOFLOW_EMAIL, ECOFLOW_PASSWORD, ECOFLOW_SERIAL, ECOFLOW_PRODUCT_TYPE=88", file=sys.stderr)
        print("\nHome Assistant:", file=sys.stderr)
        print("  HA_TOKEN, HA_URL (optional), ECOFLOW_SERIAL (optional filter)", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as err:
        print(f"HTTP error {err.code}: {err.reason}", file=sys.stderr)
        return 2
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
