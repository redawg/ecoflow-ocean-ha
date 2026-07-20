#!/usr/bin/env python3
"""Probe inverter MQTT for MPPT/string field candidates."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import time
from collections import defaultdict
from typing import Any

from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion

sys.path.insert(0, "/app/custom_components/ecoflow_ocean")

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.mqtt import _stable_client_id
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree, get_float

SN = "HR51ZA1AVH770253"
PT = "88"
LISTEN = float(os.environ.get("MQTT_DUMP_SECONDS", "25"))


def find_mppt_like(node: Any, path: str = "", out: list | None = None):
    if out is None:
        out = []
    if isinstance(node, dict):
        # mpptPv-like: has float vol/amp/pwr style fields 1,2,3
        keys = set(node.keys())
        if {1, 2, 3}.issubset(keys) or {1, 2}.issubset(keys):
            vals = {k: node[k] for k in node if isinstance(node[k], (int, float))}
            if vals and any(isinstance(node.get(k), float) for k in (1, 2, 3)):
                out.append((path, vals))
        for k, v in node.items():
            find_mppt_like(v, f"{path}.{k}" if path else str(k), out)
    return out


async def main() -> None:
    api = EcoflowOcean(
        os.environ["ECOFLOW_EMAIL"],
        os.environ["ECOFLOW_PASSWORD"],
        serial_number=SN,
        product_type=PT,
    )
    await api.login()
    auth = api._auth
    cert = auth.mqtt_cert
    uid = auth.user_id
    fields: dict[str, list[float]] = defaultdict(list)
    mppt_hits: list[tuple[str, dict]] = []

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_c, _u, msg):
        try:
            json.loads(msg.payload.decode())
            return
        except Exception:
            pass
        try:
            tree = decode_protobuf(msg.payload)
        except Exception:
            return
        flat = flatten_tree(tree)
        for path, val in flat.items():
            if isinstance(val, float) and abs(val) < 20000:
                # focus on likely MPPT band
                try:
                    last = int(path.split(".")[-1])
                except ValueError:
                    continue
                if 1470 <= last <= 1500 or last == 31 or 1 <= last <= 10:
                    fields[path].append(round(val, 2))
        hits = find_mppt_like(tree)
        if hits:
            mppt_hits.extend(hits[:20])
            print("mppt-like", hits[:8])

    def on_conn(c, _u, _f, rc, _p=None):
        if getattr(rc, "is_failure", False):
            print("FAIL", rc)
            return
        c.subscribe(
            [
                (f"/app/device/property/{SN}", 1),
                (f"/app/{uid}/{SN}/thing/property/get_reply", 1),
            ]
        )
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
        c.publish(f"/app/{uid}/{SN}/thing/property/get", req, qos=1)
        print("connected")

    client.on_connect = on_conn
    client.on_message = on_msg
    client.connect(cert.get("url") or "mqtt.ecoflow.com", int(cert.get("port") or 8883), 30)
    client.loop_start()
    await asyncio.sleep(LISTEN)
    client.loop_stop()
    client.disconnect()
    await api.close()

    print("\n=== FIELD SAMPLES ===")
    for path in sorted(fields):
        vals = fields[path][-6:]
        print(f"  {path}: {vals}")
    print(f"\nmppt-like hits: {len(mppt_hits)}")
    # unique paths
    uniq = {}
    for path, vals in mppt_hits:
        uniq[path] = vals
    for path, vals in list(uniq.items())[:40]:
        print(f"  {path}: {vals}")


if __name__ == "__main__":
    asyncio.run(main())
