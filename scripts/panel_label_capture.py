#!/usr/bin/env python3
"""Capture Ocean Panel MQTT and dump circuit label candidates."""

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
from pyecoflowocean.panel_decoder import (
    CIRCUIT_CONFIG_FIELD_END,
    CIRCUIT_CONFIG_FIELD_START,
    parse_ocean_panel_payload,
    parse_panel_flat_telemetry,
)
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree

SN = "HR61ZA1AVH7X0100"
PT = "95"
LISTEN = float(os.environ.get("MQTT_DUMP_SECONDS", "40"))


def _walk_strings(node: Any, path: str = "", out: list[tuple[str, str]] | None = None):
    if out is None:
        out = []
    if isinstance(node, str) and node.strip():
        out.append((path, node.strip()))
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk_strings(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_strings(v, f"{path}[{i}]", out)
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

    strings_by_path: dict[str, set[str]] = defaultdict(set)
    config_dumps: list[dict[str, Any]] = []
    merged_flat: dict[str, Any] = {}
    stats = {"bin": 0, "with_names": 0}

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_c, _u, msg) -> None:
        payload = msg.payload
        try:
            json.loads(payload.decode())
            return
        except Exception:
            pass
        stats["bin"] += 1
        try:
            tree = decode_protobuf(payload)
        except Exception as err:
            print("wire fail", err)
            return

        for path, text in _walk_strings(tree):
            strings_by_path[path].add(text)

        # Dump circuit config blocks 794-947 when present
        root = tree.get(1)
        if isinstance(root, dict):
            inner = root.get(1)
            if isinstance(inner, dict):
                cfg: dict[str, Any] = {}
                for field_num in range(CIRCUIT_CONFIG_FIELD_START, CIRCUIT_CONFIG_FIELD_END + 1):
                    block = inner.get(field_num)
                    if isinstance(block, dict):
                        cfg[str(field_num)] = block
                if cfg:
                    config_dumps.append(cfg)
                    print(f"config blocks in msg: {len(cfg)}")

        flat = parse_ocean_panel_payload(payload)
        if flat:
            merged_flat.update(flat)
            names = {k: v for k, v in flat.items() if k.endswith("_name")}
            if names:
                stats["with_names"] += 1
                print("decoded names", names)

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

    state = parse_panel_flat_telemetry(SN, merged_flat)
    print("\n=== STATS ===", stats)
    print("\n=== MERGED CIRCUIT NAMES (decoder) ===")
    print(json.dumps(state.circuit_names, indent=2, sort_keys=True))
    print("\n=== ALL STRING FIELDS SEEN ===")
    for path in sorted(strings_by_path):
        vals = sorted(strings_by_path[path])
        print(f"  {path}: {vals[:10]}")
    if config_dumps:
        print("\n=== LAST CONFIG DUMP (truncated) ===")
        print(json.dumps(config_dumps[-1], indent=2, default=str)[:8000])
    else:
        print("\n(no circuit config blocks 794-947 seen in this window)")


if __name__ == "__main__":
    asyncio.run(main())
