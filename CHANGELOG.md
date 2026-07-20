# Changelog

## 0.3.0

- Add EcoFlow cloud MQTT listener (`paho-mqtt`) with latestQuotas keepalive
- Prefer MQTT telemetry over empty REST quota stubs when available
- Add protobuf decoder scaffold for Power Ocean `JTS1_*` messages
- CDO Ocean Pro (type 88) confirmed to push Gen3 `cmdFunc=254` frames — mapping still WIP
- Bump iot_class to cloud_push

## 0.2.0

- Implement mobile app API client (login, US/EU region detect, provider-service telemetry)
- Parse Power Ocean energy + configuration fields from quota reports
- Config flow now requires inverter serial number and model type

## 0.1.0

- Initial scaffold for EcoFlow Power Ocean Home Assistant integration
- App-only traffic capture workflow and HAR analyzer
- Bundled `pyecoflowocean` client (awaiting API mapping)
- Read-only sensor platform and example automations
