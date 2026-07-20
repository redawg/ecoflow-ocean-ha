#!/usr/bin/env python3
"""Listen for EcoFlow MQTT telemetry and print parsed values.

Usage:
  set ECOFLOW_EMAIL, ECOFLOW_PASSWORD, ECOFLOW_SERIAL, ECOFLOW_PRODUCT_TYPE
  python scripts/mqtt_probe.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()

from pyecoflowocean import EcoflowOcean


async def main() -> int:
    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    serial = os.environ.get("ECOFLOW_SERIAL", "HR51ZA1AVH770253")
    product_type = os.environ.get("ECOFLOW_PRODUCT_TYPE", "88")

    if not email or not password:
        print("Set ECOFLOW_EMAIL and ECOFLOW_PASSWORD.", file=sys.stderr)
        print("Tip: run python scripts/setup_env.py", file=sys.stderr)
        return 1

    api = EcoflowOcean(
        email,
        password,
        serial_number=serial,
        product_type=product_type,
    )
    await api.login()
    print(f"Logged in. MQTT broker: {api._auth.mqtt_cert.get('url') if api._auth else '?'}")  # noqa: SLF001

    loop = asyncio.get_running_loop()
    update_count = 0

    async def on_update() -> None:
        nonlocal update_count
        update_count += 1
        state = api._mqtt.get_state() if api._mqtt else None  # noqa: SLF001
        if state is None:
            return
        print(f"\n--- MQTT update #{update_count} ---")
        d = state.as_dict()
        for key, value in d.items():
            if key != "serial_number":
                print(f"  {key}: {value}")
        if hasattr(state, "circuit_power_w") and state.circuit_power_w:
            top = sorted(state.circuit_power_w.items())[:8]
            print(f"  >> circuits (top): {dict(top)}")
        packs = getattr(state, "battery_packs", None) or []
        if packs:
            print(f"  >> battery packs ({len(packs)}):")
            for pack in packs:
                sn = pack.get("sn", "?")
                print(
                    f"     [{pack.get('index')}] {sn}  "
                    f"SOC={pack.get('soc')}% SOH={pack.get('soh')}%  "
                    f"P={pack.get('power_w')}W  V={pack.get('voltage_v')}V  "
                    f"I={pack.get('current_a')}A  T={pack.get('temp_c')}°C  "
                    f"remain={pack.get('remain_wh')}Wh"
                )

    await api.start_mqtt(loop, on_update=on_update)
    print(f"Listening on MQTT for {serial} (Ctrl+C to stop)...")
    print("Requesting latestQuotas every 20s; property pushes appear as they arrive.\n")

    try:
        await asyncio.sleep(90)
    except KeyboardInterrupt:
        pass
    finally:
        state = api._mqtt.get_state() if api._mqtt else None  # noqa: SLF001
        await api.close()

    if state is not None:
        print("\nFinal MQTT state:")
        for key, value in state.as_dict().items():
            if key != "serial_number":
                print(f"  {key}: {value}")
    else:
        print("\nNo MQTT telemetry received.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
