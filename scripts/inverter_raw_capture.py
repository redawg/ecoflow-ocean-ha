#!/usr/bin/env python3
"""Capture every raw + curated field from the Ocean Pro inverter's MQTT stream.

Unlike the one-shot dump/correlate scripts, this is meant to run *while you
actively change device state* (force a discharge, flip work mode, etc.) so
you can build a field->sensor mapping by watching which raw wire fields move.

Usage:
  python scripts/inverter_raw_capture.py                  # runs until Ctrl+C
  python scripts/inverter_raw_capture.py --duration 600    # stop after 10 min

While it's running:
  - Type a short note and press Enter at any time to timestamp an event
    (e.g. "started forced discharge", "switched to self-use").
  - Press Ctrl+C to stop and write the capture files.

Env vars (see .env.example): ECOFLOW_EMAIL, ECOFLOW_PASSWORD, ECOFLOW_SERIAL,
ECOFLOW_PRODUCT_TYPE, ECOFLOW_REGION.

Output (under --out-dir, default captures/inverter_raw/<timestamp>/):
  raw_capture.csv     One row per decoded MQTT message; every raw wire field
                      (raw.<dotted.path>) plus every curated/named field
                      (mapped.<key>) the integration currently decodes.
  annotations.csv     Timestamped notes you typed during the run.
  field_catalog.csv   Per-column summary (first/last/min/max/distinct count)
                      to help spot which fields moved during your test.
  run_summary.json    Start/end time, message count, serial, product type.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import ssl
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
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
from pyecoflowocean.inverter_decoder import parse_ocean_inverter_payload
from pyecoflowocean.mqtt import _stable_client_id
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree

SN = os.environ.get("ECOFLOW_SERIAL", "HR51ZA1AVH770253")
PT = os.environ.get("ECOFLOW_PRODUCT_TYPE", "88")
RAW_PREFIX = "raw."
MAPPED_PREFIX = "mapped."
META_COLUMNS = ["capture_ts", "t_offset_s", "topic", "cmd_func", "cmd_id", "msg_bytes"]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _leaf_sort_key(path: str) -> tuple[Any, ...]:
    """Sort raw.1.1.515 / raw.1.1005[0].5 style paths in a stable, readable order."""
    body = path[len(RAW_PREFIX):] if path.startswith(RAW_PREFIX) else path
    parts = re.split(r"[.\[\]]+", body)
    key: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


class Capture:
    def __init__(self, sn: str, product_type: str) -> None:
        self.sn = sn
        self.product_type = product_type
        self.start_ts = time.monotonic()
        self.start_wall = _now_iso()
        self.rows: list[dict[str, Any]] = []
        self.annotations: list[tuple[str, float, str]] = []
        self.columns: "OrderedDict[str, None]" = OrderedDict()
        self.msg_count = 0
        self.lock = threading.Lock()
        self._last_print = 0.0

    def add_note(self, text: str) -> None:
        elapsed = time.monotonic() - self.start_ts
        with self.lock:
            self.annotations.append((_now_iso(), elapsed, text))
        print(f"  [note @ {elapsed:6.1f}s] {text}")

    def add_message(self, topic: str, payload: bytes) -> None:
        try:
            json.loads(payload.decode())
            return  # JSON status frames aren't the binary telemetry we want
        except Exception:
            pass
        try:
            tree = decode_protobuf(payload)
        except Exception:
            return

        flat = flatten_tree(tree)
        cmd_func = flat.get("1.8") or flat.get("8")
        cmd_id = flat.get("1.9") or flat.get("9")

        row: dict[str, Any] = {
            "capture_ts": _now_iso(),
            "t_offset_s": round(time.monotonic() - self.start_ts, 3),
            "topic": topic,
            "cmd_func": cmd_func,
            "cmd_id": cmd_id,
            "msg_bytes": len(payload),
        }
        for path, val in flat.items():
            if isinstance(val, bool):
                row[f"{RAW_PREFIX}{path}"] = val
                continue
            if isinstance(val, (int, float)):
                if abs(float(val)) >= 1e7:
                    continue
                row[f"{RAW_PREFIX}{path}"] = val
            elif isinstance(val, str):
                row[f"{RAW_PREFIX}{path}"] = val

        curated = parse_ocean_inverter_payload(payload) or {}
        for key, val in curated.items():
            if isinstance(val, (int, float, str, bool)):
                row[f"{MAPPED_PREFIX}{key}"] = val
            elif isinstance(val, list) and key == "bp_pack_sns":
                row[f"{MAPPED_PREFIX}{key}"] = ";".join(str(v) for v in val)

        with self.lock:
            self.msg_count += 1
            self.rows.append(row)
            for col in row:
                self.columns.setdefault(col, None)

        self._maybe_print_status(row)

    def _maybe_print_status(self, row: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_print < 5:
            return
        self._last_print = now
        solar = row.get(f"{MAPPED_PREFIX}solar_power_w")
        battery = row.get(f"{MAPPED_PREFIX}battery_power_w")
        grid = row.get(f"{MAPPED_PREFIX}grid_power_w")
        soc = row.get(f"{MAPPED_PREFIX}battery_soc")
        pcs = row.get(f"{MAPPED_PREFIX}pcs_act_pwr")
        elapsed = row["t_offset_s"]
        print(
            f"  t={elapsed:7.1f}s  msgs={self.msg_count:5d}  "
            f"solar={_fmt(solar):>8}  battery={_fmt(battery):>8}  "
            f"grid={_fmt(grid):>8}  pcs={_fmt(pcs):>8}  soc={_fmt(soc):>6}"
        )

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)

        meta = [c for c in META_COLUMNS]
        raw_cols = sorted(
            (c for c in self.columns if c.startswith(RAW_PREFIX)), key=_leaf_sort_key
        )
        mapped_cols = sorted(c for c in self.columns if c.startswith(MAPPED_PREFIX))
        fieldnames = meta + raw_cols + mapped_cols

        capture_path = out_dir / "raw_capture.csv"
        with capture_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
            writer.writeheader()
            for row in self.rows:
                writer.writerow(row)

        notes_path = out_dir / "annotations.csv"
        with notes_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["capture_ts", "t_offset_s", "note"])
            for ts, elapsed, text in self.annotations:
                writer.writerow([ts, round(elapsed, 3), text])

        catalog_path = out_dir / "field_catalog.csv"
        with catalog_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["column", "kind", "samples", "first", "last", "min", "max", "distinct", "delta"]
            )
            for col in raw_cols + mapped_cols:
                values = [r[col] for r in self.rows if col in r and r[col] != ""]
                kind = "raw" if col.startswith(RAW_PREFIX) else "mapped"
                if not values:
                    continue
                numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
                distinct = len({repr(v) for v in values})
                if numeric:
                    writer.writerow(
                        [
                            col,
                            kind,
                            len(values),
                            numeric[0],
                            numeric[-1],
                            min(numeric),
                            max(numeric),
                            distinct,
                            round(numeric[-1] - numeric[0], 3),
                        ]
                    )
                else:
                    writer.writerow(
                        [col, kind, len(values), values[0], values[-1], "", "", distinct, ""]
                    )

        summary_path = out_dir / "run_summary.json"
        summary = {
            "serial_number": self.sn,
            "product_type": self.product_type,
            "start_wall": self.start_wall,
            "end_wall": _now_iso(),
            "duration_s": round(time.monotonic() - self.start_ts, 1),
            "message_count": self.msg_count,
            "row_count": len(self.rows),
            "raw_column_count": len(raw_cols),
            "mapped_column_count": len(mapped_cols),
            "annotation_count": len(self.annotations),
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print(f"\nWrote {len(self.rows)} rows / {len(fieldnames)} columns to {capture_path}")
        print(f"Wrote {len(self.annotations)} annotations to {notes_path}")
        print(f"Wrote field catalog ({len(raw_cols) + len(mapped_cols)} columns) to {catalog_path}")
        print(f"Wrote run summary to {summary_path}")


def _fmt(val: Any) -> str:
    if val is None:
        return "-"
    if isinstance(val, float):
        return f"{val:.0f}"
    return str(val)


def _stdin_notes(capture: Capture, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            line = input()
        except EOFError:
            return
        text = line.strip()
        if not text:
            continue
        if text.lower() in {"q", "quit", "stop", "exit"}:
            stop_event.set()
            return
        capture.add_note(text)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration", type=float, default=None, help="Stop after N seconds (default: run until Ctrl+C or typing 'q')"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: captures/inverter_raw/<timestamp>)",
    )
    args = parser.parse_args()

    out_dir = args.out_dir or (
        ROOT / "captures" / "inverter_raw" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )

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

    capture = Capture(SN, PT)
    stop_event = threading.Event()

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_c: Any, _u: Any, msg: Any) -> None:
        capture.add_message(msg.topic, msg.payload)

    def on_conn(c: Any, _u: Any, _f: Any, rc: Any, _p: Any = None) -> None:
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
        _publish_latest_quotas(c, uid)
        print(f"Connected. Listening for {SN} (product_type={PT}).")
        print("Type a note + Enter to mark an event, or 'q' + Enter / Ctrl+C to stop.\n")

    client.on_connect = on_conn
    client.on_message = on_msg
    client.connect(cert.get("url") or "mqtt.ecoflow.com", int(cert.get("port") or 8883), 30)
    client.loop_start()

    notes_thread = threading.Thread(target=_stdin_notes, args=(capture, stop_event), daemon=True)
    notes_thread.start()

    poll_task = asyncio.create_task(_keep_polling(client, uid, SN, stop_event))

    try:
        if args.duration:
            remaining = args.duration
            while remaining > 0 and not stop_event.is_set():
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
        else:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        poll_task.cancel()
        client.loop_stop()
        client.disconnect()
        await api.close()
        capture.write(out_dir)


def _publish_latest_quotas(client: Client, uid: str) -> None:
    payload = json.dumps(
        {
            "from": "Android",
            "id": str(int(time.time() * 1000)),
            "version": "1.0",
            "moduleType": 0,
            "operateType": "latestQuotas",
            "params": {},
        }
    )
    client.publish(f"/app/{uid}/{SN}/thing/property/get", payload, qos=1)


async def _keep_polling(client: Client, uid: str, sn: str, stop_event: threading.Event) -> None:
    try:
        while not stop_event.is_set():
            await asyncio.sleep(20)
            if client.is_connected():
                _publish_latest_quotas(client, uid)
    except asyncio.CancelledError:
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
