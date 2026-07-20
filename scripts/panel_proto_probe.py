#!/usr/bin/env python3
"""Decode raw panel MQTT protobuf payloads for schema discovery."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()


def decode_wire(data: bytes, depth: int = 0, max_depth: int = 6) -> list[tuple[int, str, object]]:
    """Generic protobuf wire decoder."""
    results: list[tuple[int, str, object]] = []
    i = 0
    indent = "  " * depth
    while i < len(data):
        try:
            tag, i = _read_varint(data, i)
        except IndexError:
            break
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:  # varint
            val, i = _read_varint(data, i)
            results.append((field_num, "varint", val))
        elif wire_type == 1:  # 64-bit
            if i + 8 > len(data):
                break
            val = struct.unpack("<d", data[i : i + 8])[0]
            results.append((field_num, "fixed64/float", val))
            i += 8
        elif wire_type == 2:  # length-delimited
            length, i = _read_varint(data, i)
            chunk = data[i : i + length]
            i += length
            # try string
            try:
                s = chunk.decode("utf-8")
                if s.isprintable() or len(s) < 64:
                    results.append((field_num, "string", s))
                    continue
            except UnicodeDecodeError:
                pass
            results.append((field_num, "bytes", chunk))
            if depth < max_depth:
                nested = decode_wire(chunk, depth + 1, max_depth)
                for nf, nt, nv in nested:
                    results.append((field_num, f"nested.{nf}.{nt}", nv))
        elif wire_type == 5:  # 32-bit
            if i + 4 > len(data):
                break
            fval = struct.unpack("<f", data[i : i + 4])[0]
            ival = struct.unpack("<I", data[i : i + 4])[0]
            results.append((field_num, "fixed32/float", fval))
            i += 4
        else:
            break
    return results


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if i >= len(data):
            raise IndexError
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def main() -> int:
    import asyncio
    import os

    from paho.mqtt.client import Client, ConnectFlags, MQTTMessage
    from paho.mqtt.enums import CallbackAPIVersion

    from pyecoflowocean import EcoflowOcean
    from pyecoflowocean.mqtt import _stable_client_id

    serial = os.environ.get("ECOFLOW_SERIAL", "HR61ZA1AVH7X0100")
    samples: list[bytes] = []

    async def run() -> None:
        api = EcoflowOcean(
            os.environ["ECOFLOW_EMAIL"],
            os.environ["ECOFLOW_PASSWORD"],
            serial_number=serial,
            product_type=os.environ.get("ECOFLOW_PRODUCT_TYPE", "95"),
        )
        await api.login()
        auth = api._auth  # noqa: SLF001
        cert = auth.mqtt_cert
        user_id = auth.user_id

        def on_message(_c: Client, _u: object, msg: MQTTMessage) -> None:
            if len(samples) < 5 and len(msg.payload) > 100:
                samples.append(bytes(msg.payload))

        client = Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=_stable_client_id(user_id),
        )
        import ssl

        client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.on_message = on_message

        def on_connect(c, _u, _f, rc, _p=None):
            if not rc.is_failure:
                c.subscribe([(f"/app/device/property/{serial}", 1)])

        client.on_connect = on_connect
        client.connect(cert["url"], int(cert["port"]), 15)
        client.loop_start()
        await asyncio.sleep(15)
        client.loop_stop()
        client.disconnect()
        await api.close()

    asyncio.run(run())

    for idx, payload in enumerate(samples):
        print(f"\n=== Sample {idx + 1} ({len(payload)} bytes) ===")
        fields = decode_wire(payload)
        for fn, ftype, val in fields:
            if ftype == "fixed32/float" and isinstance(val, float):
                if abs(val) < 100000 and (val == 0 or abs(val) > 0.001):
                    print(f"  field {fn}: {val:.4f} W/V/A?")
            elif ftype == "string":
                print(f"  field {fn}: {val!r}")
            elif "nested" in ftype and ftype.endswith("fixed32/float"):
                print(f"  {ftype}: {val:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
