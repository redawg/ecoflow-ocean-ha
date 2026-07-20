# EcoFlow Ocean LAN Web Dashboard

Read-only browser UI for your Power Ocean stack (inverter + panel + EV charger), hosted on your LAN and talking to EcoFlow cloud via the same `pyecoflowocean` client as the Home Assistant integration.

## What’s included (phases 1–3)

| Phase | Feature |
|-------|---------|
| **1** | Live power-flow dashboard + WebSocket updates |
| **2** | Ocean Panel 40 circuit grid |
| **3** | SQLite history samples → 24h/7d charts + energy kWh totals |

## Quick start (Podman on RHEL / infra3)

Build **from the repository root** (the Containerfile copies `custom_components/.../pyecoflowocean`):

```bash
cp web/.env.example web/.env
# edit web/.env — set ECOFLOW_EMAIL / ECOFLOW_PASSWORD
# For two homes: SITES=forest,desert plus SITE_DESERT_SERIALS=...
# (Forest can leave SITE_FOREST_SERIALS empty until devices are installed.)
# Save .env as UTF-8 without a BOM — a BOM makes Podman miss SITES=.

podman build --format docker -f web/Containerfile -t ecoflow-ocean-web:latest .
podman volume create ecoflow-ocean-data

podman run -d --name ecoflow-ocean-web \
  --restart=unless-stopped \
  -p 8080:8080 \
  -v ecoflow-ocean-data:/data:Z \
  --env-file web/.env \
  ecoflow-ocean-web:latest
```

Open: `http://<infra3-ip>:8080/` — use the site switcher for Forest vs Desert (CDO).

Optional auth: set `WEB_AUTH_TOKEN=secret` in `.env`, then open `http://<host>:8080/?token=secret`.

### Compose

```bash
podman compose -f web/compose.yml up -d --build
```

### systemd quadlet (persistent on RHEL)

```bash
sudo mkdir -p /etc/ecoflow-ocean-web /etc/containers/systemd
sudo cp web/.env /etc/ecoflow-ocean-web/env
sudo cp web/ecoflow-ocean-web.container /etc/containers/systemd/
sudo systemctl daemon-reload
sudo systemctl start ecoflow-ocean-web
```

## Push image to infra3

On your build machine:

```bash
podman build -f web/Containerfile -t ecoflow-ocean-web:latest .
podman save ecoflow-ocean-web:latest | gzip > ecoflow-ocean-web.tar.gz
scp ecoflow-ocean-web.tar.gz user@infra3:/tmp/
```

On infra3:

```bash
podman load -i /tmp/ecoflow-ocean-web.tar.gz
# then podman run … as above
```

If you have a registry:

```bash
podman tag ecoflow-ocean-web:latest registry.example/ecoflow-ocean-web:latest
podman push registry.example/ecoflow-ocean-web:latest
```

## Local dev (without container)

```powershell
cd web
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # fill credentials
$env:PYTHONPATH = (Get-Location).Path
$env:DATA_DIR = "$PWD\data"
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8080
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Ready / error |
| `GET /api/sites` | Configured homes + device counts |
| `GET /api/overview?site=` | Devices + power flow + panel/EV snapshot |
| `GET /api/history/power?hours=24&site=` | Bucketed watt samples |
| `GET /api/history/energy?hours=24&site=` | Integrated kWh totals |
| `GET /api/history/circuits?hours=24&site=` | Per-circuit kWh (from sampled panel watts) |
| `GET /api/history/overhead?hours=18&site=` | Panel/inverter aux + system overhead samples |
| `WS /api/ws?site=` | Live overview pushes |

## Notes

- The container still needs **outbound HTTPS/MQTT** to EcoFlow cloud (`api.ecoflow.com`, `mqtt.ecoflow.com`). It is LAN-*hosted*, not LAN-*only*.
- One shared MQTT session covers Pro + Panel + EV. Power Insight (type 105) is discovered but skipped for now.
- Solar / string tiles use a yellow intensity scale (same idea as the green grid-export card). Color steps every **250 W**; set each string’s max (S1–S5) in the Solar strings card (or `?string_max_w=3000,2500,3000,2000,1500`) so full brightness matches that string’s rating. A single `?string_max_w=3500` still applies to all five.
- **`/flow`** — Sunsynk-style animated power-flow card (strings → inverter ↔ battery/grid → house + EV). House shows essential load = site load − EV.
- History kWh is computed from sampled watts (default every 30s). Accuracy improves after the dashboard has been running.
- Circuit usage (24h / 7d / 30d) integrates panel channel watts the same way; inverter-feed channels are listed but excluded from the branch total. Tracking starts when the container is running — older periods are not backfilled.
- Panel vs inverter aux is split only overnight (`solar≈0`, not exporting): panel ≈ inverter-feed − branch circuits, inverter ≈ site home − feed. Daytime shows combined system overhead (home − branch) plus a 2.5% solar conversion estimate.
- SELinux: always use `-v ecoflow-ocean-data:/data:Z` on RHEL; without `:Z` SQLite may fail with “unable to open database file”.
- Rootless Podman: run `loginctl enable-linger $USER` so the container survives SSH logout (otherwise `/tmp/storage-run-*` teardown can stop it).
- Prefer `podman build --format docker …` so image metadata is preserved.
- When Forest devices are installed, set `SITE_FOREST_SERIALS=` to their serials (same EcoFlow account), or `SITE_FOREST_EMAIL` / `SITE_FOREST_PASSWORD` if Forest uses a different login.
