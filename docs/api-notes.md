# EcoFlow Power Ocean — API notes

Fill this document after capturing the **EcoFlow mobile app** (see [capture-traffic.md](capture-traffic.md)).

Redact tokens, passwords, and account IDs before committing.

## Capture metadata

| Field | Value |
|-------|-------|
| Date | _YYYY-MM-DD_ |
| App version | _e.g. 6.x.x_ |
| Platform | _Android / iOS_ |
| Region | US |
| HAR file | `captures/ecoflow-ocean-YYYYMMDD.har` |
| Inverter serial number | _from app device settings_ |

## Cloud hosts observed

| Host | Protocol | Purpose |
|------|----------|---------|
| | | |
| | | |

## Authentication

### Login request

```
METHOD /path
Host: ...
```

**Request body (structure):**

```json
{
  "_replace_with_redacted_sample_": "..."
}
```

**Response (token location):**

```json
{
  "_replace_with_redacted_sample_": "..."
}
```

| Item | Value |
|------|-------|
| Token type | _Bearer / cookie / signed header_ |
| Token header name | _Authorization / X-Token / ..._ |
| Refresh mechanism | _endpoint or none_ |
| Required headers | _lang, platform, app version, ..._ |

## Device discovery

### List devices

```
METHOD /path
```

**Sample response fields:**

| JSON path | Meaning |
|-----------|---------|
| | serial number |
| | device name |
| | product type |

## Telemetry

### Polling (REST)

```
METHOD /path
```

| JSON path | HA sensor | Unit | Notes |
|-----------|-----------|------|-------|
| | `battery_soc` | % | |
| | `battery_power` | W | + charge / − discharge |
| | `solar_power` | W | |
| | `grid_power` | W | + import / − export |
| | `home_power` | W | |
| | `status` | text | |

### Real-time (MQTT / WebSocket)

| Item | Value |
|------|-------|
| Host | |
| Topic pattern | |
| Payload format | _JSON / protobuf_ |
| Update interval | _seconds_ |

**Protobuf notes (if applicable):**

```
cmdFunc / message type:
sample hex:
decoded fields:
```

## Phase metrics (optional)

| JSON path | HA sensor |
|-----------|-----------|
| | `phase_a_voltage` |
| | `phase_a_current` |
| | `phase_a_power` |

## Rate limits and errors

| HTTP code | Meaning |
|-----------|---------|
| | |

## Implementation checklist

- [ ] `pyecoflowocean/auth.py` — login + token refresh
- [ ] `pyecoflowocean/client.py` — `get_devices()`, `get_system_state()`
- [ ] `pyecoflowocean/parser.py` — map JSON/protobuf fields
- [ ] `discover_devices.py` — values match app UI
- [ ] HA config flow completes on Forest Home
