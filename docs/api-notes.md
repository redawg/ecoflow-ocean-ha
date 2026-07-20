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

### CDO Ocean Pro MQTT notes (type 88)

Live capture against `HR51…` (US, `mqtt.ecoflow.com:8883`) shows:

- REST `/provider-service/user/device/detail` returns online metadata with **empty `quota`** and zero power fields.
- MQTT `/app/device/property/{sn}` pushes **binary protobuf** (not JSON).
- Decoded headers use `src=96`, `cmd_func=254`, `cmd_id=25` (Gen3), not the classic Power Ocean `cmd_func=96` / `JTS1_*` reports.
- Community Power Ocean protobuf schemas ([foxthefox/ioBroker.ecoflow-mqtt](https://github.com/foxthefox/ioBroker.ecoflow-mqtt) `ef_powerocean_data.js`) target `cmdFunc=96` and do not yet decode these CDO Gen3 frames.

Next step: reverse-engineer `cmdFunc=254` pdata for Ocean Pro (energy stream + SOC) from captured MQTT hex, then wire into `protobuf_decoder.py`.

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

## Official EcoFlow Developer API (PowerOcean / "PP2") schema

`https://developer.ecoflow.com/us/document/PP2` documents a **separate**,
official API surface (`open.ecoflow.com`, accessKey/secretKey + HMAC-SHA256
signing) distinct from the private mobile-app API we currently use
(`api.ecoflow.com`, email/password login). It's a JS SPA, so it doesn't
render via a plain HTTP fetch — needs a real browser to read.

**Confirms our existing field names/conventions:**

- `sysGridPwr` — negative = exporting (matches what we already use).
- `bpSoc`, `mpptPwr`, `sysLoadPwr`, `bpPwr` — same names we already decode.
- `pcsAPhase` / `pcsBPhase` / `pcsCPhase` — same phase objects.
- `mpptHeartBeat[].mpptPv[]` — per-string `{vol, amp, pwr}`, matches our
  per-string decoding.

> **Caveat:** a browser-extraction pass on this page also reported
> "`bpPwr`: negative = charging" — that's backwards from both our own
> passing test suite (`test_battery_pack_multi_header`, asserts
> `power_w` is negative for a pack discharging at 10 A) and the
> community-verified convention (`tolwi/hassio-ecoflow-cloud` issue #686:
> `<0 discharge / >0 charge` for the equivalent Stream AC Pro field). Treat
> that specific claim as a probable transcription error until confirmed by
> re-reading the page directly — it has **not** been applied here, and the
> positive-`bpPwr`-during-a-confirmed-discharge anomaly from the
> 2026-07-19 capture (see `docs/inverter-field-mapping.md`) is still an open
> question, not resolved by this.

**New fields not yet in our decoder** (per-phase reactive/apparent power,
plus optional add-on modules):

| Field | Type | Description |
|---|---|---|
| `pcsAPhase.reactPwr` / `pcsBPhase.reactPwr` / `pcsCPhase.reactPwr` | float | Reactive power per phase |
| `pcsAPhase.apparentPwr` / `pcsBPhase.apparentPwr` / `pcsCPhase.apparentPwr` | float | Apparent power per phase |
| `evPwr` | float | EV charger (PowerPulse) power |
| `chargingStatus` | string | EV charger status, e.g. `EV_CHG_STS_AVAILABLE` |
| `errorCode` | string | Binary-array error code |
| `sectorA.tempCurr` / `sectorB.tempCurr` | float | PowerHeat zone temperatures |
| `hpMaster.tempInlet` / `.tempOutlet` / `.tempAmbient` | float | PowerHeat heat-pump temps |
| `sectorDhw.tempCurr` | float | PowerHeat domestic hot water temp |
| `hrEnergyStream[].temp` / `.hrPwr` | float | PowerGlow (water heating rod) temp/power |
| `emsErrCode.errCode[]` | int[] | Module error codes (601 = PowerHeat, 602 = PowerGlow) |

We don't have PowerHeat/PowerGlow hardware fields wired up in
`inverter_decoder.py` — only relevant if that hardware is present.

**Official REST endpoints** (require Developer API keys, HMAC-signed):

- `POST /iot-open/sign/device/quota` — named-field snapshot (equivalent to
  our reverse-engineered protobuf decode, but JSON with stable names).
- `GET /iot-open/sign/device/quota/all` — every quota field at once.
- `POST /iot-open/sign/device/quota/data` — **historical/aggregated data**,
  max 1-week span, returns named `{indexName, indexValue, unit}` records
  (e.g. `pv_to_powerglow` in kWh). We have no equivalent today — this could
  power weekly/monthly energy totals without building our own long-term
  stats pipeline.

**Official MQTT topics** (also under the Developer API's cert scheme, not
our current one):

| Topic | Purpose |
|---|---|
| `/open/${certificateAccount}/${sn}/quota` | Device → app telemetry push |
| `/open/${certificateAccount}/${sn}/status` | Device → app online/offline (`status`: 0/1) |

**Open question:** our current mobile-app REST endpoint
(`/provider-service/user/device/detail`) returns **empty quota** for this
specific CDO Ocean Pro (product type `88`, `HR51…`) unit — that's why we
reverse-engineer the MQTT protobuf instead. It's untested whether the
*official* Developer API's `/iot-open/sign/device/quota` endpoint actually
returns populated data for this same unit. If it does, it would give clean
named JSON instead of undecoded protobuf for the fields it covers — but it
requires registering for Developer API access and reworking `auth.py` to
support HMAC-signed requests alongside (or instead of) the current
email/password login.

## References

- [niltrip/powerocean](https://github.com/niltrip/powerocean) — provider-service REST client
- [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) — mobile login + MQTT
- [foxthefox/ioBroker.ecoflow-mqtt](https://github.com/foxthefox/ioBroker.ecoflow-mqtt) — Power Ocean protobuf field docs
- [developer.ecoflow.com/us/document/PP2](https://developer.ecoflow.com/us/document/PP2) — official PowerOcean Developer API schema (requires a real browser to render)
