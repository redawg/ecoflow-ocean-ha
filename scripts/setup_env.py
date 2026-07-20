#!/usr/bin/env python3
"""Create or update .env for EcoFlow scripts and HA setup.

Usage:
  python scripts/setup_env.py
  python scripts/setup_env.py --email you@example.com --password secret

Values not passed on the command line are prompted interactively.
Existing .env keys are preserved unless overridden.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

DEFAULTS = {
    "ECOFLOW_SERIAL": "HR51ZA1AVH770253",
    "ECOFLOW_PRODUCT_TYPE": "88",
    "ECOFLOW_REGION": "us",
    "HA_URL": "http://172.16.255.250:8123",
}


def _read_existing() -> dict[str, str]:
    if not ENV_PATH.is_file():
        return {}
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _prompt(label: str, current: str = "", secret: bool = False) -> str:
    hint = f" [{current}]" if current else ""
    if secret and current:
        use = input(f"{label}{hint} (Enter to keep current): ").strip()
        return current if not use else use
    if secret:
        value = getpass.getpass(f"{label}{hint}: ")
    else:
        value = input(f"{label}{hint}: ").strip()
    return value or current


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up .env for EcoFlow Ocean HA")
    parser.add_argument("--email", help="EcoFlow app email")
    parser.add_argument("--password", help="EcoFlow app password")
    parser.add_argument("--serial", help="Inverter serial number")
    parser.add_argument("--product-type", default="88", help="Product type (88 = Ocean Pro)")
    parser.add_argument("--ha-token", help="Home Assistant long-lived access token")
    parser.add_argument("--ha-url", default="http://172.16.255.250:8123", help="HA URL")
    args = parser.parse_args()

    existing = _read_existing()
    print(f"Writing {ENV_PATH}\n")

    email = args.email or _prompt("EcoFlow email", existing.get("ECOFLOW_EMAIL", ""))
    password = args.password or _prompt(
        "EcoFlow password", existing.get("ECOFLOW_PASSWORD", ""), secret=True
    )
    serial = args.serial or _prompt(
        "Serial number", existing.get("ECOFLOW_SERIAL", DEFAULTS["ECOFLOW_SERIAL"])
    )
    product_type = args.product_type or existing.get(
        "ECOFLOW_PRODUCT_TYPE", DEFAULTS["ECOFLOW_PRODUCT_TYPE"]
    )
    region = existing.get("ECOFLOW_REGION", DEFAULTS["ECOFLOW_REGION"])
    ha_url = args.ha_url or existing.get("HA_URL", DEFAULTS["HA_URL"])
    ha_token = args.ha_token or _prompt(
        "HA long-lived token (optional)", existing.get("HA_TOKEN", ""), secret=True
    )

    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        return 1

    lines = [
        "# EcoFlow Power Ocean — local credentials (gitignored)",
        f"ECOFLOW_EMAIL={email}",
        f"ECOFLOW_PASSWORD={password}",
        f"ECOFLOW_SERIAL={serial}",
        f"ECOFLOW_PRODUCT_TYPE={product_type}",
        f"ECOFLOW_REGION={region}",
        "",
        f"HA_URL={ha_url}",
        f"HA_TOKEN={ha_token}",
        "",
    ]
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved {ENV_PATH}")
    print("\nNext steps:")
    print("  python scripts/test_login.py       # verify login")
    print("  python scripts/mqtt_probe.py       # live MQTT telemetry")
    print("  python scripts/discover_devices.py # REST telemetry dump")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
