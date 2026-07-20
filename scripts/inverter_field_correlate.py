#!/usr/bin/env python3
"""Correlate Ocean Pro MQTT wire fields with solar/grid/battery/home.

Stops briefly conflicting with the dashboard MQTT session if run on infra3
with MQTT_KILL_WEB=1.

Usage (container or host with env):
  MQTT_DUMP_SECONDS=45 python scripts/inverter_field_correlate.py
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

from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "custom_components" / "ecoflow_ocean", Path("/app/scripts"), Path("/app/custom_components/ecoflow_ocean")):
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
LISTEN = float(os.environ.get("MQTT_DUMP_SECONDS", "45"))


def leaf_field(path: str) -> int | None:
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

    # path -> list of numeric samples
    samples: dict[str, list[float]] = defaultdict(list)
    cmd_ids: set[tuple[Any, Any]] = set()
    msg_count = 0

    # EcoFlow certs bind to a single stable client_id — do not suffix it.
    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_c, _u, msg) -> None:
        nonlocal msg_count
        payload = msg.payload
        try:
            json.loads(payload.decode())
            return
        except Exception:
            pass
        try:
            tree = decode_protobuf(payload)
        except Exception:
            return
        msg_count += 1
        flat = flatten_tree(tree)
        # Header cmd_func / cmd_id often at 1.8 / 1.9 or similar
        for path, val in flat.items():
            if path.endswith(".8") and isinstance(val, int):
                cf = val
            if path.endswith(".9") and isinstance(val, int):
                pass
        cf = flat.get("1.8") or flat.get("8")
        cid = flat.get("1.9") or flat.get("9")
        if isinstance(cf, int) or isinstance(cid, int):
            cmd_ids.add((cf, cid))
        for path, val in flat.items():
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                fval = float(val)
                if abs(fval) > 1e7:
                    continue
                samples[path].append(fval)

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
        print("connected; listening", LISTEN, "s")

    client.on_connect = on_conn
    client.on_message = on_msg
    client.connect(cert.get("url") or "mqtt.ecoflow.com", int(cert.get("port") or 8883), 30)
    client.loop_start()
    await asyncio.sleep(LISTEN)
    client.loop_stop()
    client.disconnect()
    await api.close()

    print(f"\nBinary messages: {msg_count}")
    print(f"cmd_func/cmd_id pairs: {sorted(cmd_ids)}")

    # Summarize by leaf field number (max abs, mean of last 5, variance)
    by_leaf: dict[int, list[tuple[str, float, float, float]]] = defaultdict(list)
    for path, vals in samples.items():
        leaf = leaf_field(path)
        if leaf is None or not vals:
            continue
        recent = vals[-8:]
        mean = sum(recent) / len(recent)
        mx = max(abs(v) for v in recent)
        # skip constants that never move and are tiny integers only
        uniq = {round(v, 2) for v in recent}
        by_leaf[leaf].append((path, mean, mx, float(len(uniq))))

    # Candidates for power-like values
    print("\n=== Power-like leaf fields (|mean| 20..20000, varies) ===")
    powerish: list[tuple[float, int, str, float, float]] = []
    for leaf, entries in sorted(by_leaf.items()):
        for path, mean, mx, nuniq in entries:
            if 20 <= abs(mean) <= 20000 and nuniq >= 2:
                powerish.append((abs(mean), leaf, path, mean, mx))
            elif 50 <= mx <= 20000 and abs(mean) >= 5 and nuniq >= 2:
                powerish.append((mx, leaf, path, mean, mx))
    powerish.sort(reverse=True)
    for score, leaf, path, mean, mx in powerish[:80]:
        print(f"  f{leaf:4d}  mean={mean:9.2f}  maxabs={mx:9.2f}  {path}")

    # Known string fields
    print("\n=== Known MPPT string fields 1480-1484 ===")
    for leaf in range(1480, 1485):
        for path, mean, mx, nuniq in by_leaf.get(leaf, []):
            print(f"  f{leaf} mean={mean:.2f} max={mx:.2f} uniq={nuniq} {path}")

    # SOC-like 0-100 integers that vary
    print("\n=== SOC-like (0..100, varies) ===")
    for leaf, entries in sorted(by_leaf.items()):
        for path, mean, mx, nuniq in entries:
            if 0 <= mean <= 100 and mx <= 100 and nuniq >= 2:
                recent = samples[path][-8:]
                if all(abs(v - round(v)) < 0.05 for v in recent) or all(0 <= v <= 100 for v in recent):
                    if abs(mean) > 0.5:
                        print(f"  f{leaf:4d} mean={mean:6.2f} uniq={nuniq} {path} samples={recent[-5:]}")

    # Energy balance hunt: find quartets s,g,b,h where s+g+b ≈ h (within 15%)
    print("\n=== Energy balance candidates (solar+grid+battery≈home) ===")
    # Use latest snapshot across all paths: take last value per path
    latest: dict[str, float] = {p: vals[-1] for p, vals in samples.items() if vals}
    # Restrict to leaf fields in interesting bands
    candidates = []
    for path, val in latest.items():
        leaf = leaf_field(path)
        if leaf is None:
            continue
        if 20 <= abs(val) <= 20000 or (0 <= abs(val) <= 20000 and leaf >= 10):
            candidates.append((leaf, path, val))

    # Prefer distinct leaf numbers near known clusters
    interesting_leaves = {
        leaf for leaf, _p, val in candidates if 30 <= abs(val) <= 15000
    }
    # Also include near-zero possible grid/battery
    for leaf, path, val in candidates:
        if abs(val) < 30 and leaf in {1, 2, 3, 4, 5, 10, 36, 45, 53, 59, 84, 109, 1227, 1467, 1468}:
            interesting_leaves.add(leaf)

    leaf_vals: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for leaf, path, val in candidates:
        if leaf in interesting_leaves or leaf in range(1480, 1485):
            leaf_vals[leaf].append((path, val))

    # Pick one representative value per leaf (prefer path under 1.1)
    rep: dict[int, float] = {}
    for leaf, items in leaf_vals.items():
        items_sorted = sorted(items, key=lambda x: (0 if "1.1." in x[0] else 1, x[0]))
        rep[leaf] = items_sorted[0][1]

    solar_sum = sum(abs(rep.get(f, 0.0)) for f in range(1480, 1485) if f in rep)
    print(f"String sum 1480-1484 = {solar_sum:.1f} W")

    # Score each leaf as potential grid/battery/home given solar_sum
    scored = []
    for leaf, val in rep.items():
        if leaf in range(1480, 1485):
            continue
        scored.append((abs(val), leaf, val))
    scored.sort(reverse=True)
    print("Top magnitude non-string leaves (latest):")
    for mag, leaf, val in scored[:40]:
        print(f"  f{leaf:4d} = {val:9.2f}")

    # Try combinations among top leaves for balance with solar_sum
    top = [leaf for _, leaf, _ in scored[:25]]
    hits = []
    for g in top:
        for b in top:
            if b == g:
                continue
            for h in top:
                if h in (g, b):
                    continue
                s = solar_sum
                gv, bv, hv = rep[g], rep[b], rep[h]
                # Convention A: home = solar + grid + battery
                pred = s + gv + bv
                err = abs(pred - hv)
                if abs(hv) > 50 and err < max(80, 0.2 * abs(hv)):
                    hits.append((err, "s+g+b=h", g, b, h, s, gv, bv, hv))
                # Convention B: home = solar + grid - battery (if bp sign flipped)
                pred2 = s + gv - bv
                err2 = abs(pred2 - hv)
                if abs(hv) > 50 and err2 < max(80, 0.2 * abs(hv)):
                    hits.append((err2, "s+g-b=h", g, b, h, s, gv, bv, hv))
                # Convention C: abs usage
                pred3 = s + gv + bv
                err3 = abs(abs(pred3) - abs(hv))
                if abs(hv) > 50 and err3 < max(80, 0.2 * abs(hv)):
                    hits.append((err3, "|s+g+b|=|h|", g, b, h, s, gv, bv, hv))
    hits.sort(key=lambda x: x[0])
    for hit in hits[:20]:
        err, how, g, b, h, s, gv, bv, hv = hit
        print(
            f"  err={err:5.1f} {how:12s} grid=f{g}({gv:.1f}) bat=f{b}({bv:.1f}) "
            f"home=f{h}({hv:.1f}) solar={s:.1f}"
        )

    # Dump EMS-proto field numbers if present as nested (1-109)
    print("\n=== Nested EMS-like low fields under 1.1 (if any) ===")
    for path, vals in sorted(samples.items()):
        leaf = leaf_field(path)
        if leaf is None or leaf > 120:
            continue
        if not path.startswith("1."):
            continue
        recent = vals[-5:]
        mean = sum(recent) / len(recent)
        if abs(mean) > 0.01:
            print(f"  {path} mean={mean:.3f} last={recent[-1]:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
