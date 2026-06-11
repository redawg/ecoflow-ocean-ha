#!/usr/bin/env python3
"""Probe Forest Home API for EcoFlow Ocean integration install."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HA_URL", "http://172.16.255.250:8123")
TOKEN = os.environ.get("HA_TOKEN", "")


def get(path: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return err.code, data


def main() -> int:
    if not TOKEN:
        print("Set HA_TOKEN (long-lived access token from HA profile).", file=sys.stderr)
        return 1

    checks = [
        "/api/",
        "/api/config",
        "/api/config/config_entries/entry",
    ]
    for path in checks:
        code, data = get(path)
        print(f"\n=== {path} -> {code} ===")
        if path.endswith("/entry") and isinstance(data, list):
            domains = sorted({entry.get("domain") for entry in data})
            print("domains:", ", ".join(domains[:40]), ("..." if len(domains) > 40 else ""))
            ecoflow_entries = [e for e in data if e.get("domain") == "ecoflow_ocean"]
            if ecoflow_entries:
                print("ecoflow_ocean: ALREADY CONFIGURED")
                for entry in ecoflow_entries:
                    print(f"  - {entry.get('title')} ({entry.get('entry_id')})")
            else:
                print("ecoflow_ocean: not configured yet")
        elif isinstance(data, dict):
            print(json.dumps(data, indent=2)[:1200])
        else:
            print(str(data)[:500])

    code, states = get("/api/states")
    print(f"\n=== /api/states (ecoflow_ocean) -> {code} ===")
    if isinstance(states, list):
        matches = [
            s for s in states if str(s.get("entity_id", "")).startswith("sensor.")
            and "ecoflow" in str(s.get("entity_id", "")).lower()
        ]
        if matches:
            for state in matches[:20]:
                print(f"  {state['entity_id']}: {state.get('state')}")
        else:
            print("  (no ecoflow_ocean entities yet)")

    print(
        "\nDeploy checklist:"
        "\n  1. Install via HACS or copy custom_components/ecoflow_ocean to /config/custom_components/"
        "\n  2. Restart Home Assistant"
        "\n  3. Settings → Devices & services → Add integration → EcoFlow Power Ocean"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
