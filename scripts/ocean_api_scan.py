"""Scan EcoFlow Ocean REST endpoints + short MQTT realtime check.

Usage:
  python scripts/ocean_api_scan.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()

from pyecoflowocean.client import EcoflowOcean  # noqa: E402
from pyecoflowocean.const import AUTH_HOST, DEFAULT_TIMEOUT  # noqa: E402

OCEAN_DEVICES = [
    ("HR51ZA1AVH770253", "88", "inverter"),
    ("HR61ZA1AVH7X0100", "95", "panel"),
    ("C102ZA1AZH6G0018", "99", "ev_charger"),
]

# Candidate paths seen in community reverse-engineering / app traffic.
# {sn} and {pt} are substituted.
CANDIDATES: list[tuple[str, str, str]] = [
    # (host_key, method, path)
    ("data", "GET", "/provider-service/user/device/detail?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/version?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/quota?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/property?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/status?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/realtime?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/energy?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/history?sn={sn}"),
    ("data", "GET", "/provider-service/user/device/data?sn={sn}"),
    ("data", "GET", "/provider-service/device/detail?sn={sn}"),
    ("data", "GET", "/provider-service/device/quota?sn={sn}"),
    ("data", "GET", "/provider-service/app/device/detail?sn={sn}"),
    ("auth", "GET", "/iot-service/user/device"),
    ("auth", "GET", "/iot-service/user/device/detail?sn={sn}"),
    ("auth", "GET", "/iot-service/device/quota/all?sn={sn}"),
    ("auth", "GET", "/iot-service/device/quota?sn={sn}"),
    ("auth", "GET", "/iot-service/device/property?sn={sn}"),
    ("auth", "GET", "/iot-service/device/property/latest?sn={sn}"),
    ("auth", "GET", "/iot-open/device/quota?sn={sn}"),
    ("auth", "GET", "/iot-open/device/quota/all?sn={sn}"),
    ("data", "GET", "/iot-open/device/quota?sn={sn}"),
    ("data", "GET", "/iot-open/device/quota/all?sn={sn}"),
    ("auth", "GET", "/app/device/quota?sn={sn}"),
    ("data", "GET", "/app/device/quota?sn={sn}"),
    ("auth", "GET", "/device/quota/all?sn={sn}"),
    ("data", "GET", "/device/quota/all?sn={sn}"),
]


def _summarize(body: Any, limit: int = 280) -> str:
    try:
        text = json.dumps(body, default=str, separators=(",", ":"))
    except TypeError:
        text = repr(body)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _interesting_keys(body: Any) -> list[str]:
    keys: list[str] = []
    if not isinstance(body, dict):
        return keys
    data = body.get("data")
    if isinstance(data, dict):
        keys.extend(sorted(data.keys())[:40])
        quota = data.get("quota")
        if isinstance(quota, dict):
            keys.append(f"quota[{len(quota)}]")
            keys.extend(f"quota.{k}" for k in list(quota.keys())[:12])
    return keys


async def probe_rest(api: EcoflowOcean) -> None:
    auth = await api._get_auth()  # noqa: SLF001
    await auth.ensure_token()
    session = await api._get_session()  # noqa: SLF001
    hosts = {
        "auth": f"https://{AUTH_HOST}",
        "data": f"https://{api.api_host or 'api-a.ecoflow.com'}",
    }

    print("\n======== REST endpoint scan ========")
    for sn, product_type, kind in OCEAN_DEVICES:
        print(f"\n--- {kind} {sn} product-type={product_type} ---")
        # Ensure region host is detected for this product.
        try:
            await api.get_raw_telemetry(sn, product_type=product_type)
            hosts["data"] = f"https://{api.api_host}"
        except Exception as err:  # noqa: BLE001
            print(f"  region detect note: {err}")

        for host_key, method, path_tmpl in CANDIDATES:
            path = path_tmpl.format(sn=sn, pt=product_type)
            url = hosts[host_key] + path
            headers = {
                **auth.auth_headers(),
                "product-type": product_type,
                "user-agent": "EcoFlow/OceanApiScan",
            }
            t0 = time.perf_counter()
            try:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    try:
                        body = await resp.json(content_type=None)
                    except Exception:  # noqa: BLE001
                        text = await resp.text()
                        body = {"_text": text[:200]}
                    code = body.get("code") if isinstance(body, dict) else None
                    msg = body.get("message") if isinstance(body, dict) else None
                    data = body.get("data") if isinstance(body, dict) else None
                    has_data = data not in (None, {}, [], "")
                    keys = _interesting_keys(body)
                    marker = "HIT" if resp.status == 200 and has_data else (
                        "OK-empty" if resp.status == 200 else "MISS"
                    )
                    print(
                        f"  [{marker}] {resp.status} {elapsed_ms:5.0f}ms "
                        f"{host_key} {method} {path}"
                    )
                    if code is not None or msg:
                        print(f"         code={code!r} message={msg!r}")
                    if keys:
                        print(f"         keys: {keys}")
                    if has_data:
                        print(f"         sample: {_summarize(body)}")
            except Exception as err:  # noqa: BLE001
                print(f"  [ERR] {host_key} {method} {path} -> {err}")


async def probe_mqtt(api: EcoflowOcean, seconds: float = 35.0) -> None:
    print("\n======== MQTT realtime probe ========")
    loop = asyncio.get_running_loop()
    counts = {sn: 0 for sn, _, _ in OCEAN_DEVICES}
    last: dict[str, dict[str, Any]] = {}

    # Use multi-device MQTT via temporary EcoflowOcean per device is heavy;
    # instead start one listener per device sequentially is slow. Prefer
    # shared approach using existing mqtt from first device then subscribe all.
    # Simplest reliable path: start_mqtt on inverter, then manually add topics
    # is complex. Probe each device for a short window.

    for sn, product_type, kind in OCEAN_DEVICES:
        print(f"\n--- MQTT {kind} {sn} ({seconds:.0f}s) ---")
        device_api = EcoflowOcean(
            os.environ["ECOFLOW_EMAIL"],
            os.environ["ECOFLOW_PASSWORD"],
            region=os.environ.get("ECOFLOW_REGION", "us"),
            serial_number=sn,
            product_type=product_type,
        )
        await device_api.login()
        msg_count = 0

        async def on_update(serial: str = sn) -> None:
            nonlocal msg_count
            msg_count += 1
            state = device_api._mqtt.get_state() if device_api._mqtt else None  # noqa: SLF001
            if state is None:
                print(f"  update #{msg_count}: (parse returned None)")
                return
            d = state.as_dict()
            last[serial] = d
            interesting = {
                k: v
                for k, v in d.items()
                if k
                in (
                    "battery_soc",
                    "solar_power_w",
                    "grid_power_w",
                    "home_power_w",
                    "battery_power_w",
                    "grid_voltage_v",
                    "grid_import_power_w",
                    "charge_power_w",
                    "vehicle_connected",
                    "charging_active",
                    "storm_enabled",
                    "online",
                    "updated_at",
                )
                and v is not None
            }
            circuits = getattr(state, "circuit_power_w", None) or {}
            if circuits:
                interesting["circuits"] = len(circuits)
            print(f"  update #{msg_count}: {interesting}")

        try:
            await device_api.start_mqtt(loop, on_update=on_update)
            await asyncio.sleep(seconds)
        finally:
            await device_api.close()

        counts[sn] = msg_count
        print(f"  -> decoded updates in {seconds:.0f}s: {msg_count}")

    print("\n======== Summary ========")
    print("REST: see HIT lines above (non-empty data payloads).")
    print("MQTT decoded updates:")
    for sn, _, kind in OCEAN_DEVICES:
        print(f"  {kind} {sn}: {counts[sn]}")
    for sn, d in last.items():
        print(f"  last[{sn}]: {_summarize(d, 400)}")


async def main() -> int:
    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    if not email or not password:
        print("Set ECOFLOW_EMAIL and ECOFLOW_PASSWORD", file=sys.stderr)
        return 1

    api = EcoflowOcean(
        email,
        password,
        region=os.environ.get("ECOFLOW_REGION", "us"),
        serial_number=OCEAN_DEVICES[0][0],
        product_type=OCEAN_DEVICES[0][1],
    )
    await api.login()
    print(f"Logged in. auth={AUTH_HOST} data_host={api.api_host or '(pending)'}")
    try:
        await probe_rest(api)
        await probe_mqtt(api, seconds=float(os.environ.get("MQTT_SCAN_SECONDS", "25")))
    finally:
        await api.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
