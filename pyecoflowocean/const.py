"""EcoFlow API constants — update after app traffic capture."""

from __future__ import annotations

# Expected US cloud hosts (confirm from HAR capture).
ECOFLOW_HOSTS = (
    "api.ecoflow.com",
    "api-us.ecoflow.com",
    "mqtt.ecoflow.com",
    "mqtt-us.ecoflow.com",
)

DEFAULT_REGION = "us"
DEFAULT_TIMEOUT = 30

# Placeholders — fill from docs/api-notes.md after capture.
AUTH_LOGIN_PATH = "/auth/login"  # TODO: replace with captured path
DEVICES_PATH = "/devices"  # TODO: replace with captured path
TELEMETRY_PATH = "/devices/{sn}/status"  # TODO: replace with captured path

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    # Common EcoFlow app headers — confirm values from capture:
    "lang": "en-US",
    "platform": "android",
}
