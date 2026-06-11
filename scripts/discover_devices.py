#!/usr/bin/env python3
"""List Power Ocean devices and telemetry keys from your EcoFlow account.

Usage:
  python scripts/discover_devices.py

Environment variables:
  ECOFLOW_EMAIL
  ECOFLOW_PASSWORD
  ECOFLOW_REGION       (optional, default: us)
  ECOFLOW_DUMP_JSON=1  (optional, print raw payloads)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from pyecoflowocean import ApiNotMappedError, EcoflowOcean
from pyecoflowocean.client import filter_power_ocean_devices


async def main() -> int:
    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    region = os.environ.get("ECOFLOW_REGION", "us")
    dump_json = os.environ.get("ECOFLOW_DUMP_JSON")

    if not email or not password:
        print(
            "Set ECOFLOW_EMAIL and ECOFLOW_PASSWORD environment variables.",
            file=sys.stderr,
        )
        return 1

    api = EcoflowOcean(email, password, region=region)
    try:
        await api.login()
        print(f"Logged in to EcoFlow ({region})\n")

        devices = await api.get_devices()
        devices = filter_power_ocean_devices(devices)

        if not devices:
            print("No devices returned.")
            return 0

        for device in devices:
            print(f"Device: {device.name}")
            print(f"  serial: {device.serial_number}")
            print(f"  type:   {device.product_type}")

            if dump_json:
                print("  raw discovery:")
                print(json.dumps(device.raw, indent=2, default=str)[:2000])

            try:
                state = await api.get_system_state(device.serial_number)
            except ApiNotMappedError as err:
                print(f"  telemetry: NOT MAPPED ({err})")
                continue

            print("  parsed telemetry:")
            for key, value in state.as_dict().items():
                if key != "serial_number":
                    print(f"    {key}: {value}")

            if dump_json and state.raw:
                print("  raw telemetry:")
                print(json.dumps(state.raw, indent=2, default=str)[:2000])
            print()
    except ApiNotMappedError as err:
        print(f"API not mapped yet: {err}", file=sys.stderr)
        print(
            "\nCapture the EcoFlow mobile app first — see docs/capture-traffic.md",
            file=sys.stderr,
        )
        return 2
    finally:
        await api.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
