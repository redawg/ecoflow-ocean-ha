#!/usr/bin/env python3
"""Summarize EcoFlow API calls from a HAR export (mobile app only).

Export a HAR file from mitmproxy or HTTP Toolkit while using the EcoFlow
Android/iOS app on a proxied device.

Usage:
  python scripts/analyze_har.py captures/ecoflow-ocean-YYYYMMDD.har
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from urllib.parse import urlparse

ECOFLOW_HOST_PATTERN = re.compile(r"ecoflow", re.IGNORECASE)
SENSITIVE_HEADERS = {"authorization", "cookie", "x-signature", "x-token"}


def _header_map(headers: list[dict]) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in headers}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/analyze_har.py <file.har>", file=sys.stderr)
        return 1

    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        har = json.load(fh)

    entries = har.get("log", {}).get("entries", [])
    hosts = Counter()
    endpoints = Counter()
    methods = Counter()
    ecoflow_calls: list[dict] = []
    auth_headers_seen: Counter[str] = Counter()
    mqtt_hosts: set[str] = set()

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        method = req.get("method", "")
        parsed = urlparse(url)
        hosts[parsed.netloc] += 1
        methods[method] += 1

        if not ECOFLOW_HOST_PATTERN.search(parsed.netloc):
            continue

        if parsed.scheme in {"ws", "wss"} or "mqtt" in parsed.netloc:
            mqtt_hosts.add(parsed.netloc)

        path_only = parsed.path
        endpoints[f"{method} {path_only}"] += 1

        headers = _header_map(req.get("headers", []))
        for name in headers:
            if name in SENSITIVE_HEADERS or "auth" in name or "token" in name:
                auth_headers_seen[name] += 1

        post_data = req.get("postData", {})
        body = post_data.get("text")
        ecoflow_calls.append(
            {
                "method": method,
                "url": url,
                "body": body[:400] if body else None,
                "status": entry.get("response", {}).get("status"),
                "req_headers": {
                    k: v[:80] for k, v in headers.items() if k in auth_headers_seen
                },
            }
        )

    print(f"Total requests: {len(entries)}\n")
    print("Top hosts:")
    for host, count in hosts.most_common(15):
        marker = "  <-- EcoFlow" if ECOFLOW_HOST_PATTERN.search(host) else ""
        print(f"  {host}: {count}{marker}")

    print("\nEcoFlow REST endpoints:")
    if endpoints:
        for endpoint, count in endpoints.most_common(30):
            print(f"  {endpoint}: {count}")
    else:
        print("  (none found — check proxy/cert setup on the phone)")

    if mqtt_hosts:
        print("\nPossible MQTT / WebSocket hosts:")
        for host in sorted(mqtt_hosts):
            print(f"  {host}")

    print("\nAuth-related request headers seen:")
    for header, count in auth_headers_seen.most_common():
        print(f"  {header}: {count}")

    print("\nSample EcoFlow calls:")
    for call in ecoflow_calls[:20]:
        print(f"  {call['status']} {call['method']} {call['url']}")
        if call["body"]:
            print(f"    body: {call['body']}")
        if call["req_headers"]:
            print(f"    headers: {call['req_headers']}")

    print(
        "\nNext steps:"
        "\n  1. Copy auth + device list + telemetry endpoints into docs/api-notes.md"
        "\n  2. Implement pyecoflowocean/auth.py and client.py"
        "\n  3. Run: ECOFLOW_EMAIL=... ECOFLOW_PASSWORD=... python scripts/discover_devices.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
