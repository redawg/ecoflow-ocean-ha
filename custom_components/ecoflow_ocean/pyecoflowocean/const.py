"""EcoFlow API constants — update after app traffic capture."""

from __future__ import annotations

ECOFLOW_HOSTS = (
    "api.ecoflow.com",
    "api-us.ecoflow.com",
    "mqtt.ecoflow.com",
    "mqtt-us.ecoflow.com",
)

DEFAULT_REGION = "us"
DEFAULT_TIMEOUT = 30

AUTH_LOGIN_PATH = "/auth/login"
DEVICES_PATH = "/devices"
TELEMETRY_PATH = "/devices/{sn}/status"

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "lang": "en-US",
    "platform": "android",
}
