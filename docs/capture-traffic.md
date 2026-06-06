# Capturing EcoFlow mobile app traffic (US)

Existing community integrations do not work for Power Ocean on your system. Before the Home Assistant integration can talk to EcoFlow, we reverse-engineer the **mobile app API** from captured HTTPS traffic.

**App-only:** Capture the EcoFlow Android or iOS app. Do not use the web portal for this project.

## Prerequisites

- EcoFlow app installed and logged in (US account)
- PC on the same network as your phone
- HTTP Toolkit **or** mitmproxy

## Option A: HTTP Toolkit (recommended)

1. Install [HTTP Toolkit](https://httptoolkit.com/) on your PC.
2. Choose **Android device via ADB** or **Manual Android setup**.
3. Install the HTTPS certificate on your phone when prompted.
4. Open the EcoFlow app and perform the actions below while recording.
5. Filter for hosts containing `ecoflow`.
6. Export the session as HAR to `captures/ecoflow-ocean-YYYYMMDD.har`.
7. Run:

```powershell
pip install -r requirements-dev.txt
python scripts/analyze_har.py captures/ecoflow-ocean-YYYYMMDD.har
```

## Option B: mitmproxy

1. Install mitmproxy: `pip install mitmproxy`
2. Start it: `mitmweb --listen-port 8080`
3. On your phone, set Wi-Fi proxy to your PC IP, port 8080.
4. Browse to `http://mitm.it` on the phone and install the Android/iOS cert.
5. Use the EcoFlow app normally.
6. Export HAR from mitmweb.

## What to do in the app while recording

Perform these steps in order so auth and telemetry are easy to find in the HAR:

| Step | Action |
|------|--------|
| 1 | Log out (if already logged in), then log in fresh |
| 2 | Open the Power Ocean home / energy flow screen |
| 3 | Wait 2–3 minutes (live power updates) |
| 4 | Pull to refresh |
| 5 | Switch tabs: battery, solar, grid (if separate screens) |
| 6 | Open device settings and note your inverter **serial number** |

## Expected hosts (US — confirm from your capture)

| Host | Likely role |
|------|-------------|
| `api.ecoflow.com` | Primary REST API |
| `api-us.ecoflow.com` | Possible US shard |
| `mqtt.ecoflow.com` | Real-time push (MQTT over WebSocket) |
| `mqtt-us.ecoflow.com` | Possible US MQTT shard |

Record whatever the app actually hits — shard names vary by account and app version.

## What we need from the capture

Document findings in [api-notes.md](api-notes.md):

- **Auth** — login URL, request body, token type (Bearer / signed headers / cookies)
- **Device list** — how the app resolves your Power Ocean serial number
- **Telemetry** — REST polling vs MQTT topic; update interval
- **Payload format** — JSON field names or protobuf (note `cmdFunc` / message types if binary)
- **Required headers** — `lang`, `platform`, app version, device id, etc.

## Certificate pinning

If the EcoFlow app refuses to connect through the proxy (no `ecoflow` hosts in the HAR):

1. Confirm the system cert is trusted on the phone (Android 7+ may need a user cert + network security config).
2. Try an Android emulator with mitmproxy and a debuggable build (advanced — document only if needed).
3. Share the failed HAR anyway — DNS lookups and partial handshakes still help.

## After capture

1. Fill in `docs/api-notes.md` (redact passwords/tokens).
2. Implement `pyecoflowocean/auth.py` and `client.py`.
3. Verify live values:

```powershell
$env:ECOFLOW_EMAIL = "you@example.com"
$env:ECOFLOW_PASSWORD = "your-password"
$env:ECOFLOW_DUMP_JSON = "1"
python scripts/discover_devices.py
```

Values from `discover_devices.py` should match the app within ~30 seconds.
