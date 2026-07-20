"""Dump all available Ocean Pro inverter data (REST + MQTT).

Usage:
  PYTHONPATH=custom_components/ecoflow_ocean python scripts/inverter_full_dump.py
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from paho.mqtt.client import Client, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()

from pyecoflowocean import EcoflowOcean  # noqa: E402
from pyecoflowocean.mqtt import _stable_client_id  # noqa: E402
from pyecoflowocean.parser import parse_mqtt_payload  # noqa: E402
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree  # noqa: E402

SN = os.environ.get("ECOFLOW_SERIAL", "HR51ZA1AVH770253")
PT = os.environ.get("ECOFLOW_PRODUCT_TYPE", "88")
LISTEN_S = float(os.environ.get("MQTT_DUMP_SECONDS", "40"))


def _pp(obj: Any, limit: int = 12000) -> str:
    text = json.dumps(obj, indent=2, default=str)
    return text if len(text) <= limit else text[:limit] + "\n… [truncated]"


async def dump_rest(api: EcoflowOcean) -> None:
    print("\n" + "=" * 72)
    print(f"REST — inverter {SN} product-type={PT}")
    print("=" * 72)

    detail = await api.get_raw_telemetry(SN, product_type=PT)
    print("\n--- GET /provider-service/user/device/detail ---")
    print(_pp(detail))

    auth = await api._get_auth()  # noqa: SLF001
    await auth.ensure_token()
    session = await api._get_session()  # noqa: SLF001
    host = api.api_host or "api-a.ecoflow.com"
    headers = {**auth.auth_headers(), "product-type": PT, "user-agent": "EcoFlow/InverterDump"}

    for label, path in (
        ("property", f"/provider-service/user/device/property?sn={SN}"),
        ("version", f"/provider-service/user/device/version?sn={SN}"),
    ):
        url = f"https://{host}{path}"
        async with session.get(url, headers=headers, timeout=20) as resp:
            body = await resp.json(content_type=None)
            print(f"\n--- GET {path} ({resp.status}) ---")
            print(_pp(body))

    # Bound device entry for this SN
    devices = await api.get_devices()
    mine = [d for d in devices if d.serial_number == SN]
    print("\n--- Device list entry ---")
    if mine:
        d = mine[0]
        print(_pp({"serial": d.serial_number, "name": d.name, "product_type": d.product_type, "raw": d.raw}))
    else:
        print("(not in filtered Ocean list — dumping raw bound entry)")
        from pyecoflowocean.const import AUTH_HOST, DEFAULT_TIMEOUT, DEVICE_LIST_PATH

        url = f"https://{AUTH_HOST}{DEVICE_LIST_PATH}"
        async with session.get(url, headers=auth.auth_headers(), timeout=DEFAULT_TIMEOUT) as resp:
            body = await resp.json(content_type=None)
        bound = (body.get("data") or {}).get("bound") or {}
        print(_pp(bound.get(SN)))


async def dump_mqtt(api: EcoflowOcean) -> None:
    print("\n" + "=" * 72)
    print(f"MQTT — listen {LISTEN_S:.0f}s for {SN}")
    print("=" * 72)

    auth = api._auth  # noqa: SLF001
    assert auth is not None
    cert = auth.mqtt_cert
    user_id = auth.user_id
    assert cert and user_id

    fields_seen: dict[str, set[Any]] = defaultdict(set)
    parsed_updates: list[dict[str, Any]] = []
    msg_stats = {"json": 0, "binary": 0, "parse_ok": 0, "parse_fail": 0}
    topics_seen: set[str] = set()

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(user_id) + "_invdump",
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_message(_c: Client, _u: object, message: MQTTMessage) -> None:
        topics_seen.add(message.topic)
        payload = message.payload
        try:
            data = json.loads(payload.decode("utf-8"))
            msg_stats["json"] += 1
            print(f"\n[JSON] {message.topic} ({len(payload)} B)")
            print(_pp(data, 4000))
            return
        except Exception:
            pass

        msg_stats["binary"] += 1
        print(f"\n[BIN] {message.topic} ({len(payload)} B) hex={payload[:48].hex()}…")
        try:
            tree = decode_protobuf(payload)
            flat = flatten_tree(tree)
            for path, value in flat.items():
                if isinstance(value, float):
                    if abs(value) > 1e7:
                        continue
                    fields_seen[path].add(round(value, 4))
                elif isinstance(value, (int, bool)):
                    fields_seen[path].add(value)
                elif isinstance(value, str) and len(value) <= 80:
                    fields_seen[path].add(value)
        except Exception as err:  # noqa: BLE001
            print(f"  wire decode error: {err}")

        try:
            update = parse_mqtt_payload(payload, SN, PT)
            if update:
                msg_stats["parse_ok"] += 1
                parsed_updates.append(update)
                print(f"  parsed keys: {sorted(update.keys())}")
            else:
                msg_stats["parse_fail"] += 1
                print("  parser: no mapped fields")
        except Exception as err:  # noqa: BLE001
            msg_stats["parse_fail"] += 1
            print(f"  parser error: {err}")

    def on_connect(c: Client, _u: object, _f: object, rc: object, _p: object = None) -> None:
        if getattr(rc, "is_failure", False):
            print("MQTT connect failed:", rc)
            return
        topics = [
            (f"/app/device/property/{SN}", 1),
            (f"/app/device/status/{SN}", 1),
            (f"/app/{user_id}/{SN}/thing/property/get_reply", 1),
            (f"/app/{user_id}/{SN}/thing/property/set_reply", 1),
        ]
        c.subscribe(topics)
        print("Subscribed:", [t[0] for t in topics])
        req = json.dumps(
            {
                "from": "Android",
                "id": str(int(time.time() * 1000)),
                "version": "1.0",
                "moduleType": 0,
                "operateType": "latestQuotas",
                "params": {},
            }
        )
        c.publish(f"/app/{user_id}/{SN}/thing/property/get", req, qos=1)
        print("Published latestQuotas get")

    client.on_connect = on_connect
    client.on_message = on_message
    host = cert.get("url") or "mqtt.ecoflow.com"
    port = int(cert.get("port") or 8883)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        await asyncio.sleep(LISTEN_S)
        # refresh quotas once more mid-listen
        await asyncio.sleep(0)
    finally:
        client.loop_stop()
        client.disconnect()

    print("\n--- MQTT message stats ---")
    print(_pp(msg_stats))
    print("topics:", sorted(topics_seen))

    print("\n--- Accumulated protobuf field paths (unique values sampled) ---")
    if not fields_seen:
        print("(none decoded)")
    else:
        for path in sorted(fields_seen):
            vals = sorted(fields_seen[path], key=lambda v: (str(type(v)), str(v)))
            show = vals[:8]
            extra = f" (+{len(vals) - 8} more)" if len(vals) > 8 else ""
            print(f"  {path}: {show}{extra}")

    print("\n--- Merged parsed MQTT updates ---")
    merged: dict[str, Any] = {}
    for upd in parsed_updates:
        merged.update(upd)
    print(_pp(merged) if merged else "(parser produced no inverter fields)")

    # Also try high-level client MQTT state if start_mqtt works briefly
    print("\n--- High-level EcoflowOcean.get_system_state (REST+any MQTT) ---")
    try:
        state = await api.get_system_state(SN, product_type=PT)
        print(_pp(state.as_dict()))
        if getattr(state, "raw", None):
            print("raw keys:", list(state.raw.keys()) if isinstance(state.raw, dict) else type(state.raw))
    except Exception as err:  # noqa: BLE001
        print(f"get_system_state error: {err}")


async def main() -> int:
    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    if not email or not password:
        print("Set ECOFLOW_EMAIL / ECOFLOW_PASSWORD", file=sys.stderr)
        return 1

    api = EcoflowOcean(email, password, region=os.environ.get("ECOFLOW_REGION", "us"), serial_number=SN, product_type=PT)
    await api.login()
    print(f"Logged in. data_host={api.api_host}")
    try:
        await dump_rest(api)
        await dump_mqtt(api)
    finally:
        await api.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
