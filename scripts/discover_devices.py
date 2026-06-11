#!/usr/bin/env python3
"""List Power Ocean devices and telemetry keys from your EcoFlow account.

Usage:
  python scripts/discover_devices.py

Environment variables:
  ECOFLOW_EMAIL
  ECOFLOW_PASSWORD
  ECOFLOW_SERIAL          (optional — lists account devices if omitted)
  ECOFLOW_PRODUCT_TYPE    (optional, default: 83; use 88 for OCEAN Pro)
  ECOFLOW_REGION          (optional, default: us)
  ECOFLOW_DUMP_JSON=1     (optional, print raw payloads)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.const import PRODUCT_TYPE_NAMES, PRODUCT_TYPE_POWER_OCEAN


async def main() -> int:
    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    serial = os.environ.get("ECOFLOW_SERIAL")
    product_type = os.environ.get("ECOFLOW_PRODUCT_TYPE", PRODUCT_TYPE_POWER_OCEAN)
    region = os.environ.get("ECOFLOW_REGION", "us")
    dump_json = os.environ.get("ECOFLOW_DUMP_JSON")

    if not email or not password:
        print(
            "Set ECOFLOW_EMAIL and ECOFLOW_PASSWORD.",
            file=sys.stderr,
        )
        return 1

    api = EcoflowOcean(
        email,
        password,
        region=region,
        serial_number=serial,
        product_type=product_type,
    )
    try:
        await api.login()
        print(f"Logged in to EcoFlow ({region})")

        devices = await api.get_devices()
        if devices:
            print("\nAccount devices (Ocean / inverter candidates):")
            for device in devices:
                label = PRODUCT_TYPE_NAMES.get(device.product_type, device.product_type)
                print(f"  {device.serial_number}  {device.name}  [{label}]")

        if not serial:
            if len(devices) == 1:
                serial = devices[0].serial_number
                product_type = devices[0].product_type
                print(f"\nUsing sole device: {serial} (product-type {product_type})")
            else:
                print(
                    "\nSet ECOFLOW_SERIAL (and ECOFLOW_PRODUCT_TYPE if not 83) "
                    "to fetch telemetry.",
                    file=sys.stderr,
                )
                return 0

        print(f"API host: {api.api_host}")
        print(f"Serial:   {serial}")
        print(f"Model:    {product_type}\n")

        state = await api.get_system_state(serial, product_type=product_type)
        print("Parsed telemetry:")
        for key, value in state.as_dict().items():
            if key != "serial_number":
                print(f"  {key}: {value}")

        if dump_json:
            raw = await api.get_raw_telemetry(serial, product_type=product_type)
            print("\nRaw provider-service response:")
            print(json.dumps(raw, indent=2, default=str)[:8000])
        print()
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    finally:
        await api.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
