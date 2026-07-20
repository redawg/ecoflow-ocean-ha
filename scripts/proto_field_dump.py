#!/usr/bin/env python3
"""Dump decoded protobuf fields from EcoFlow MQTT (45s)."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from collections import defaultdict
from pathlib import Path

from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.mqtt import _stable_client_id
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree

SN = os.environ.get("ECOFLOW_SERIAL", "HR61ZA1AVH7X0100")
fields_seen: dict[str, set] = defaultdict(set)


def on_message(client: Client, userdata: object, message) -> None:
    payload = message.payload
    try:
        json.loads(payload.decode("utf-8"))
        return
    except Exception:
        pass
    try:
        tree = decode_protobuf(payload)
        for path, value in flatten_tree(tree).items():
            if isinstance(value, float):
                if abs(value) > 1e6 or (0 < abs(value) < 1e-6 and value != 0):
                    continue
                fields_seen[path].add(round(value, 3))
            elif isinstance(value, (int, bool, str)):
                if isinstance(value, str) and len(value) > 80:
                    continue
                fields_seen[path].add(value)
    except ValueError:
        pass


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    email = os.environ["ECOFLOW_EMAIL"]
    password = os.environ["ECOFLOW_PASSWORD"]
    pt = os.environ.get("ECOFLOW_PRODUCT_TYPE", "95")
    api = EcoflowOcean(email, password, serial_number=SN, product_type=pt)
    await api.login()
    auth = api._auth  # noqa: SLF001
    cert = auth.mqtt_cert
    user_id = auth.user_id

    client = Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=_stable_client_id(user_id))
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    client.on_message = on_message

    def on_connect(c, u, flags, rc, properties=None):
        if rc.is_failure:
            print("CONNECT FAILED", rc)
            return
        topics = [
            (f"/app/device/property/{SN}", 1),
            (f"/app/device/status/{SN}", 1),
            (f"/app/{user_id}/{SN}/thing/property/get_reply", 1),
        ]
        c.subscribe(topics)
        req = json.dumps(
            {
                "from": "Android",
                "id": "999000124",
                "version": "1.0",
                "moduleType": 0,
                "operateType": "latestQuotas",
                "params": {},
            }
        )
        c.publish(f"/app/{user_id}/{SN}/thing/property/get", req, qos=1)

    client.on_connect = on_connect
    client.connect(cert["url"], int(cert["port"]), 15)
    client.loop_start()
    print(f"Dumping fields for {SN} (type {pt}) for 45s...")
    await asyncio.sleep(45)
    client.loop_stop()
    client.disconnect()
    await api.close()

    for path in sorted(fields_seen, key=lambda p: (len(p.split(".")), p)):
        vals = sorted(fields_seen[path], key=lambda x: (isinstance(x, str), x))[:8]
        extra = f" (+{len(fields_seen[path]) - 8})" if len(fields_seen[path]) > 8 else ""
        print(f"{path}: {vals}{extra}")
    print(f"\nTotal field paths: {len(fields_seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
