#!/usr/bin/env python3
"""Probe EcoFlow Power Ocean Modbus TCP (local port 502)."""

from __future__ import annotations

import argparse
import asyncio
import socket
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from env_loader import load_dotenv

load_dotenv()

# Community register map (EF-PowerOcean-TcpModbus / EcoFlow_PowerOcean_Modbus.md)
REG_DEVICE_TYPE = 40001
REG_SERIAL = 40004
REG_STATUS = 42081
REG_SOC = 42082
REG_PV_POWER = 40574
REG_BAT_POWER = 40576
REG_GRID_POWER = 40596
REG_GRID_V_L1 = 40580


def read_uint16(client, addr: int, slave: int = 1) -> int | None:
    # pymodbus 3.x uses `device_id`; 2.x uses `slave`
    kwargs = {"address": addr - 40001, "count": 1}
    try:
        result = client.read_holding_registers(**kwargs, device_id=slave)
    except TypeError:
        result = client.read_holding_registers(**kwargs, slave=slave)
    if result.isError():
        return None
    return result.registers[0]


def read_float_word_swapped(client, addr: int, scale: float = 1.0, slave: int = 1) -> float | None:
    kwargs = {"address": addr - 40001, "count": 2}
    try:
        result = client.read_holding_registers(**kwargs, device_id=slave)
    except TypeError:
        result = client.read_holding_registers(**kwargs, slave=slave)
    if result.isError():
        return None
    raw = struct.pack(">HH", result.registers[1], result.registers[0])
    value = struct.unpack(">f", raw)[0]
    return round(value * scale, 3)


def read_serial(client, slave: int = 1) -> str | None:
    kwargs = {"address": REG_SERIAL - 40001, "count": 8}
    try:
        result = client.read_holding_registers(**kwargs, device_id=slave)
    except TypeError:
        result = client.read_holding_registers(**kwargs, slave=slave)
    if result.isError():
        return None
    chars: list[str] = []
    for val in result.registers:
        hi = (val >> 8) & 0xFF
        lo = val & 0xFF
        if 32 <= hi <= 126:
            chars.append(chr(hi))
        if 32 <= lo <= 126:
            chars.append(chr(lo))
    text = "".join(chars).strip()
    return text or None


def port_open(host: str, port: int = 502, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_subnet(prefix: str, start: int = 1, end: int = 254, *, workers: int = 64) -> list[str]:
    import concurrent.futures

    open_hosts: list[str] = []

    def check(last: int) -> str | None:
        host = f"{prefix}.{last}"
        return host if port_open(host, timeout=0.4) else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(check, range(start, end + 1)):
            if result:
                open_hosts.append(result)
                print(f"  port 502 open: {result}")
    return sorted(open_hosts, key=lambda h: [int(x) for x in h.split(".")])


def probe_modbus(host: str, expected_sn: str | None = None) -> dict[str, object]:
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(host, port=502, timeout=3)
    if not client.connect():
        return {"host": host, "error": "connect failed"}

    out: dict[str, object] = {"host": host}
    try:
        out["device_type"] = read_uint16(client, REG_DEVICE_TYPE)
        out["serial"] = read_serial(client)
        out["status"] = read_uint16(client, REG_STATUS)
        out["soc"] = read_uint16(client, REG_SOC)
        out["pv_power_w"] = read_float_word_swapped(client, REG_PV_POWER, scale=100)
        out["battery_power_w"] = read_float_word_swapped(client, REG_BAT_POWER, scale=1000)
        out["grid_power_w"] = read_float_word_swapped(client, REG_GRID_POWER, scale=10)
        out["grid_voltage_l1_v"] = read_float_word_swapped(client, REG_GRID_V_L1)
        if expected_sn and out.get("serial") and expected_sn not in str(out["serial"]):
            out["serial_match"] = False
        elif expected_sn:
            out["serial_match"] = True
    finally:
        client.close()
    return out


async def cloud_device_hints() -> list[str]:
    import os

    email = os.environ.get("ECOFLOW_EMAIL")
    password = os.environ.get("ECOFLOW_PASSWORD")
    if not email or not password:
        return []

    from pyecoflowocean import EcoflowOcean

    hints: list[str] = []
    api = EcoflowOcean(email, password, serial_number=os.environ.get("ECOFLOW_SERIAL", ""))
    await api.login()
    try:
        raw = await api.get_raw_telemetry(
            os.environ.get("ECOFLOW_SERIAL", ""),
            product_type=os.environ.get("ECOFLOW_PRODUCT_TYPE", "88"),
        )
        data = raw.get("data") if isinstance(raw, dict) else {}

        def walk(obj, path: str = "") -> None:
            if isinstance(obj, dict):
                for key, val in obj.items():
                    key_l = str(key).lower()
                    p = f"{path}.{key}" if path else str(key)
                    if any(token in key_l for token in ("ip", "wifi", "lan", "local")):
                        hints.append(f"{p}={val!r}")
                    walk(val, p)
            elif isinstance(obj, list):
                for idx, val in enumerate(obj):
                    walk(val, f"{path}[{idx}]")

        walk(data if isinstance(data, dict) else raw)
    finally:
        await api.close()
    return hints


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe EcoFlow Modbus TCP")
    parser.add_argument("--host", help="Device IP (skip scan)")
    parser.add_argument("--scan", help="Scan subnet prefix, e.g. 172.16.255")
    parser.add_argument("--expected-sn", default=None)
    args = parser.parse_args()

    try:
        import pymodbus  # noqa: F401
    except ImportError:
        print("Install pymodbus: pip install 'pymodbus>=3.6,<3.8'")
        return 1

    import os

    expected_sn = args.expected_sn or os.environ.get("ECOFLOW_SERIAL")

    hints = asyncio.run(cloud_device_hints())
    if hints:
        print("Cloud API IP-related fields:")
        for line in hints[:20]:
            print(f"  {line}")

    hosts: list[str] = []
    if args.host:
        hosts = [args.host]
    elif args.scan:
        print(f"Scanning {args.scan}.0/24 for Modbus TCP (502)...")
        hosts = scan_subnet(args.scan)
    else:
        # Default: infer from HA_URL subnet if set
        ha_url = os.environ.get("HA_URL", "http://172.16.255.250:8123")
        try:
            host_part = ha_url.split("//", 1)[1].split(":", 1)[0]
            prefix = ".".join(host_part.split(".")[:3])
        except IndexError:
            prefix = "172.16.255"
        print(f"No --host given; scanning {prefix}.0/24 for port 502...")
        hosts = scan_subnet(prefix)

    if not hosts:
        print("\nNo hosts with port 502 open.")
        print("Modbus must be enabled by EcoFlow/installer on the inverter.")
        return 2

    ok = False
    for host in hosts:
        print(f"\nProbing Modbus at {host}...")
        result = probe_modbus(host, expected_sn)
        for key, val in result.items():
            print(f"  {key}: {val}")
        if result.get("serial") or result.get("soc") is not None:
            ok = True

    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
