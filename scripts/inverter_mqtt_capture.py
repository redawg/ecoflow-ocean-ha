#!/usr/bin/env python3
"""One-shot inverter MQTT field dump (run when no other client holds the session)."""

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
sys.path.insert(0, "/app/web")

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.mqtt import _stable_client_id
from pyecoflowocean.parser import parse_mqtt_payload
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree

SN = "HR51ZA1AVH770253"
PT = "88"
LISTEN = float(os.environ.get("MQTT_DUMP_SECONDS", "30"))


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

    fields: dict[str, set[Any]] = defaultdict(set)
    parsed: list[dict[str, Any]] = []
    stats = {"json": 0, "bin": 0, "ok": 0, "fail": 0}
    topics: set[str] = set()

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_c, _u, msg) -> None:
        topics.add(msg.topic)
        payload = msg.payload
        try:
            data = json.loads(payload.decode())
            stats["json"] += 1
            print("JSON", msg.topic, json.dumps(data, default=str)[:800])
            return
        except Exception:
            pass
        stats["bin"] += 1
        print(f"BIN {msg.topic} {len(payload)}B")
        try:
            for path, val in flatten_tree(decode_protobuf(payload)).items():
                if isinstance(val, float):
                    if abs(val) < 1e7:
                        fields[path].add(round(val, 4))
                elif isinstance(val, (int, bool)):
                    fields[path].add(val)
                elif isinstance(val, str) and len(val) <= 60:
                    fields[path].add(val)
        except Exception as err:
            print(" wire:", err)
        try:
            upd = parse_mqtt_payload(payload, SN, PT)
            if upd:
                stats["ok"] += 1
                parsed.append(upd)
                print(" PARSED", sorted(upd.keys()))
            else:
                stats["fail"] += 1
        except Exception as err:
            stats["fail"] += 1
            print(" parse:", err)

    def on_conn(c, _u, _f, rc, _p=None) -> None:
        if getattr(rc, "is_failure", False):
            print("CONNECT FAIL", rc)
            return
        c.subscribe(
            [
                (f"/app/device/property/{SN}", 1),
                (f"/app/device/status/{SN}", 1),
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

    merged: dict[str, Any] = {}
    for upd in parsed:
        merged.update(upd)

    print("\n=== STATS ===")
    print(json.dumps({"stats": stats, "topics": sorted(topics)}, indent=2))
    print("\n=== MERGED PARSED FIELDS ===")
    print(json.dumps(merged, indent=2, default=str))
    packish_paths = {
        p: sorted(fields[p], key=lambda v: (str(type(v)), str(v)))[:12]
        for p in sorted(fields)
        if any(
            token in p
            for token in (
                "1005",
                "1006",
                "1007",
                "1008",
                ".10",
                ".20",
                "HR52",
                "HR5",
            )
        )
        or any(
            isinstance(v, float) and 95.0 <= v <= 100.0
            for v in fields[p]
        )
    }
    print(f"\n=== PACK-ISH PATHS ({len(packish_paths)}) ===")
    for path, vals in packish_paths.items():
        print(f"  {path}: {vals}")
    print(f"\n=== PROTOBUF PATHS ({len(fields)}) ===")
    for path in sorted(fields):
        vals = sorted(fields[path], key=lambda v: (str(type(v)), str(v)))[:8]
        print(f"  {path}: {vals}")

    out_dir = os.environ.get("CAPTURE_OUT", "/out")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "merged.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "stats": stats,
                    "merged": merged,
                    "packish_paths": {k: [str(x) for x in v] for k, v in packish_paths.items()},
                    "bp_packs": merged.get("bp_packs"),
                    "battery_soc": merged.get("battery_soc") or merged.get("bpSoc"),
                },
                fh,
                indent=2,
                default=str,
            )
        print(f"\nWrote {out_dir}/merged.json")
    except Exception as err:
        print("write out failed:", err)


if __name__ == "__main__":
    asyncio.run(main())
