# EcoFlow Power Ocean — API notes (mobile app reverse engineering)

Documented from community reverse engineering ([niltrip/powerocean](https://github.com/niltrip/powerocean), [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud), [foxthefox/ioBroker.ecoflow-mqtt](https://github.com/foxthefox/ioBroker.ecoflow-mqtt)) and implemented in `pyecoflowocean`.

Redact tokens before committing HAR captures.

## Protocol summary

| Layer | Details |
|-------|---------|
| Auth host | `https://api.ecoflow.com` (global) |
| Data host (US) | `https://api-a.ecoflow.com` |
| Data host (EU) | `https://api-e.ecoflow.com` |
| Credentials | EcoFlow **app email + password** (not developer API keys) |
| Password encoding | Base64 of plaintext password in login JSON |
| Session | Bearer token from login |
| Live updates | MQTT via `/iot-auth/app/certification` (optional; REST polling implemented first) |

## Authentication

### Login

```
POST https://api.ecoflow.com/auth/login
Content-Type: application/json
lang: en_US
```

**Request body:**

```json
{
  "email": "you@example.com",
  "password": "<base64-encoded-password>",
  "scene": "IOT_APP",
  "userType": "ECOFLOW"
}
```

**Response fields:**

| Path | Use |
|------|-----|
| `data.token` | Bearer token for REST |
| `data.user.userId` | MQTT topics + certification |

### MQTT certification (real-time path)

```
GET https://api.ecoflow.com/iot-auth/app/certification?userId={userId}
Authorization: Bearer {token}
```

Returns `data.url`, `data.port`, `data.certificateAccount`, `data.certificatePassword`.

MQTT topics (private app API):

| Topic | Purpose |
|-------|---------|
| `/app/device/property/{sn}` | Live push |
| `/app/{userId}/{sn}/thing/property/get` | Request snapshot |
| `/app/{userId}/{sn}/thing/property/set` | Write configuration |

Payloads may be JSON or base64-wrapped protobuf (`HeaderMessage`).

## Device discovery

There is no reliable public mobile list endpoint for all accounts. Setup uses:

- **Serial number** from EcoFlow app → device settings
- **Product type** header (Power Ocean model code)

| Model | `product-type` header |
|-------|----------------------|
| Power Ocean | `83` |
| Power Ocean DC Fit | `85` |
| Power Ocean Single Phase | `86` |
| Power Ocean Plus | `87` |
| Power Ocean Pro (CDO) | `88` |

Account device list (works on CDO Ocean installs):

```
GET https://api.ecoflow.com/iot-service/user/device
Authorization: Bearer {token}
```

Returns `data.bound` — a dict keyed by serial number with `deviceName`, `productType`, `online`, etc.
Use product type **88** for CDO OCEAN Pro inverters (`HR51…` serials).

## Telemetry (REST — implemented)

```
GET https://{api-a|api-e}.ecoflow.com/provider-service/user/device/detail?sn={serial}
Authorization: Bearer {token}
product-type: 83
```

**Response:** `data` object with top-level energy fields plus nested `data.quota` reports.

### Primary report blocks

| Report | Key fields |
|--------|------------|
| Top-level `data` | `bpSoc`, `bpPwr`, `mpptPwr`, `sysGridPwr`, `sysLoadPwr`, `online` |
| `JTS1_ENERGY_STREAM_REPORT` | Same energy flow fields |
| `ParallelEnergyStreamReport` | Multi-inverter / parallel installs |
| `JTS1_EMS_HEARTBEAT` | `pcsAPhase`, `pcsBPhase`, `pcsCPhase`, `mpptHeartBeat`, `pcsMeterPower` |
| `JTS1_EMS_CHANGE_REPORT` | `emsWordMode`, `sysBatChgUpLimit`, `sysBatDsgDownLimit`, `emsFeedPwr`, `emsFeedRatio` |

### Energy field mapping

| HA sensor | JSON keys | Notes |
|-----------|-----------|-------|
| `battery_soc` | `bpSoc` | % |
| `battery_power` | `bpPwr`, `emsBpPower` | W; + charge / − discharge |
| `solar_power` | `mpptPwr`, `pvInvPwr` | W |
| `grid_power` | `sysGridPwr`, `pcsMeterPower` | W; + import / − export |
| `home_power` | `sysLoadPwr` | W |
| `work_mode` | `emsWordMode` | e.g. `WORKMODE_SELFUSE` |
| `backup_soc_limit` | `sysBatChgUpLimit` | % |
| `discharge_soc_limit` | `sysBatDsgDownLimit` | % |
| `feed_power_limit` | `emsFeedPwr` | W |
| `feed_ratio` | `emsFeedRatio` | % |

### Phase metrics (`JTS1_EMS_HEARTBEAT`)

| Phase | Keys |
|-------|------|
| A | `pcsAPhase.vol`, `.amp`, `.actPwr` |
| B | `pcsBPhase.*` |
| C | `pcsCPhase.*` |

## Configuration writes (not yet implemented)

Configuration changes in the app use MQTT publish to:

```
/app/{userId}/{sn}/thing/property/set
```

Writable fields live mainly in `JTS1_EMS_CHANGE_REPORT` (work mode, backup SOC, export limits). v1 integration is **read-only**.

## Verify locally

```powershell
$env:ECOFLOW_EMAIL = "you@example.com"
$env:ECOFLOW_PASSWORD = "your-password"
$env:ECOFLOW_SERIAL = "HJ31..."
$env:ECOFLOW_SERIAL = "HR51..."
$env:ECOFLOW_PRODUCT_TYPE = "88"
python scripts/discover_devices.py
```

Values should match the EcoFlow app energy screen within ~30 seconds.

## References

- [niltrip/powerocean](https://github.com/niltrip/powerocean) — provider-service REST client
- [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) — mobile login + MQTT
- [foxthefox/ioBroker.ecoflow-mqtt](https://github.com/foxthefox/ioBroker.ecoflow-mqtt) — Power Ocean protobuf field docs
