#!/usr/bin/env python3
"""Dump one representative Ocean Pro MQTT frame per cmd_id with labeled guesses."""

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

from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion

ROOT = Path(__file__).resolve().parents[1]
for p in (
    ROOT / "scripts",
    ROOT / "custom_components" / "ecoflow_ocean",
    Path("/app/scripts"),
    Path("/app/custom_components/ecoflow_ocean"),
):
    if p.exists():
        sys.path.insert(0, str(p))

try:
    from env_loader import load_dotenv

    load_dotenv()
except Exception:
    pass

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.mqtt import _stable_client_id
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree

SN = os.environ.get("ECOFLOW_SERIAL", "HR51ZA1AVH770253")
PT = os.environ.get("ECOFLOW_PRODUCT_TYPE", "88")
LISTEN = float(os.environ.get("MQTT_DUMP_SECONDS", "60"))

# Working hypotheses from CDO capture (cmdFunc 254 / live floats under 1.1).
GUESSES: dict[int, str] = {
    21: "pcs_act_pwr? / AC total (+)",
    22: "small signed (grid offset?)",
    53: "pcs_act_pwr? / AC total (-mirror)",
    515: "large signed twin of 517",
    516: "mid power (~load leftover?)",
    517: "large positive twin",
    1463: "pcsA.vol",
    1464: "pcsA.amp",
    1465: "pcsB.vol",
    1466: "pcsB.amp",
    1467: "pcsA.actPwr",
    1468: "pcsB.actPwr",
    1469: "pcs freq? or SOC?",
    1476: "aggregate power?",
    1477: "aggregate power?",
    1478: "aggregate power?",
    1479: "aggregate power?",
    1480: "mppt string1 W",
    1481: "mppt string2 W",
    1482: "mppt string3 W",
    1483: "mppt string4 W",
    1484: "mppt string5 W",
    1553: "temp or pack SOC?",
    1554: "temp or pack SOC?",
    1555: "temp or pack SOC?",
    1556: "temp or pack SOC?",
}


def leaf(path: str) -> int | None:
    try:
        return int(str(path).rsplit(".", 1)[-1])
    except ValueError:
        return None


async def main() -> None:
    api = EcoflowOcean(
        os.environ["ECOFLOW_EMAIL"],
        os.environ["ECOFLOW_PASSWORD"],
        serial_number=SN,
        product_type=PT,
    )
    await api.login()
    auth = api._auth
    assert auth and auth.mqtt_cert and auth.user_id
    cert = auth.mqtt_cert
    uid = auth.user_id

    frames: dict[tuple[int, int], dict[str, float]] = {}
    series: dict[int, list[float]] = defaultdict(list)

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_c, _u, msg) -> None:
        try:
            json.loads(msg.payload.decode())
            return
        except Exception:
            pass
        try:
            flat = flatten_tree(decode_protobuf(msg.payload))
        except Exception:
            return
        cf = flat.get("1.8")
        cid = flat.get("1.9")
        if not isinstance(cf, int) or not isinstance(cid, int):
            return
        snap: dict[str, float] = {}
        for path, val in flat.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                fval = float(val)
                if abs(fval) < 1e7:
                    snap[path] = fval
                    lf = leaf(path)
                    if lf is not None and path.startswith("1.1."):
                        series[lf].append(fval)
        key = (cf, cid)
        # Keep the richest frame
        if key not in frames or len(snap) > len(frames[key]):
            frames[key] = snap

    def on_conn(c, _u, _f, rc, _p=None) -> None:
        if getattr(rc, "is_failure", False):
            print("CONNECT FAIL", rc)
            return
        c.subscribe(
            [
                (f"/app/device/property/{SN}", 1),
                (f"/app/{uid}/{SN}/thing/property/get_reply", 1),
            ]
        )
        for _ in range(3):
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

    print("Frames by cmdFunc/cmdId:", sorted(frames))
    for key in sorted(frames):
        snap = frames[key]
        print(f"\n======== cmdFunc={key[0]} cmdId={key[1]} fields={len(snap)} ========")
        # Prefer 1.1.* numeric leaves
        rows = []
        for path, val in snap.items():
            if not path.startswith("1.1."):
                continue
            lf = leaf(path)
            if lf is None:
                continue
            rows.append((lf, path, val))
        rows.sort()
        for lf, path, val in rows:
            guess = GUESSES.get(lf, "")
            mark = f"  << {guess}" if guess else ""
            if abs(val) >= 1 or guess:
                print(f"  {path:18s} {val:12.3f}{mark}")

        # Derived
        def g(n: int) -> float | None:
            return snap.get(f"1.1.{n}")

        s = sum(abs(g(n) or 0) for n in range(1480, 1485))
        a = g(1467)
        b = g(1468)
        print("--- derived ---")
        print(f"  string_sum={s:.1f}")
        if a is not None and b is not None:
            print(f"  phase_act_sum={a+b:.1f}  (1467+1468)")
        for n in (21, 22, 53, 515, 516, 517, 1476, 1477, 1478, 1479):
            if g(n) is not None:
                print(f"  f{n}={g(n):.1f}")

    # Cross-check stability for SOC candidates: low variance integers 5-100
    print("\n======== SOC candidates (stable 5..100) ========")
    for lf, vals in sorted(series.items()):
        if len(vals) < 3:
            continue
        mean = sum(vals) / len(vals)
        if not (5 <= mean <= 100):
            continue
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        if var < 25:
            print(f"  f{lf}: mean={mean:.2f} var={var:.2f} n={len(vals)} last={vals[-1]:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
