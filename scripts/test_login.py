#!/usr/bin/env python3
"""Login and telemetry smoke test for pyecoflowocean.

Usage:
  set ECOFLOW_EMAIL, ECOFLOW_PASSWORD, ECOFLOW_SERIAL (optional for login-only)
  python scripts/test_login.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from pyecoflowocean import EcoflowOcean, InvalidCredentialsError
from pyecoflowocean.const import PRODUCT_TYPE_POWER_OCEAN


async def main() -> int:
    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    serial = os.environ.get("ECOFLOW_SERIAL")
    product_type = os.environ.get("ECOFLOW_PRODUCT_TYPE", PRODUCT_TYPE_POWER_OCEAN)

    if not email or not password:
        print("Missing ECOFLOW_EMAIL or ECOFLOW_PASSWORD.", file=sys.stderr)
        return 1

    api = EcoflowOcean(
        email,
        password,
        serial_number=serial,
        product_type=product_type,
    )
    try:
        print("1. Login...")
        await api.login()
        auth = api._auth  # noqa: SLF001
        print(f"   OK — user: {auth.user_name if auth else '?'}")
        if auth and auth.mqtt_cert:
            print(f"   MQTT broker: {auth.mqtt_cert.get('url')}:{auth.mqtt_cert.get('port')}")
        else:
            print("   MQTT cert: not returned")

        if not serial:
            print("\n2. Telemetry skipped (set ECOFLOW_SERIAL to test)")
            return 0

        print(f"\n2. Region host: {api.api_host}")
        print("3. Fetch telemetry...")
        state = await api.get_system_state(serial, product_type=product_type)
        print("   OK — parsed values:")
        for key, value in state.as_dict().items():
            if key not in {"serial_number", "raw"}:
                print(f"     {key}: {value}")
        return 0
    except InvalidCredentialsError as err:
        print(f"LOGIN FAILED: {err}", file=sys.stderr)
        return 2
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    finally:
        await api.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
