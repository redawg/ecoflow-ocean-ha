"""EcoFlow mobile app API constants (reverse-engineered)."""

from __future__ import annotations

AUTH_HOST = "api.ecoflow.com"
AUTH_LOGIN_PATH = "/auth/login"
MQTT_CERT_PATH = "/iot-auth/app/certification"
DEVICE_DETAIL_PATH = "/provider-service/user/device/detail"

# Mobile app bound devices (dict keyed by serial number).
DEVICE_LIST_PATH = "/iot-service/user/device"

REGION_HOSTS = {
    "us": "api-a.ecoflow.com",
    "eu": "api-e.ecoflow.com",
}

DEFAULT_REGION = "us"
DEFAULT_TIMEOUT = 30

# EcoFlow app product-type header values (Power Ocean variants).
PRODUCT_TYPE_POWER_OCEAN = "83"
PRODUCT_TYPE_POWER_OCEAN_DC_FIT = "85"
PRODUCT_TYPE_POWER_OCEAN_SINGLE_PHASE = "86"
PRODUCT_TYPE_POWER_OCEAN_PLUS = "87"
PRODUCT_TYPE_POWER_OCEAN_PRO = "88"
PRODUCT_TYPE_OCEAN_PANEL = "95"
PRODUCT_TYPE_EV_CHARGER = "99"

PRODUCT_TYPE_NAMES = {
    PRODUCT_TYPE_POWER_OCEAN: "Power Ocean",
    PRODUCT_TYPE_POWER_OCEAN_DC_FIT: "Power Ocean DC Fit",
    PRODUCT_TYPE_POWER_OCEAN_SINGLE_PHASE: "Power Ocean Single Phase",
    PRODUCT_TYPE_POWER_OCEAN_PLUS: "Power Ocean Plus",
    PRODUCT_TYPE_POWER_OCEAN_PRO: "Power Ocean Pro",
    PRODUCT_TYPE_OCEAN_PANEL: "Ocean Smart Panel 40",
    PRODUCT_TYPE_EV_CHARGER: "Ocean EV Charger",
}

POWER_OCEAN_PRODUCT_TYPES = tuple(
    k for k in PRODUCT_TYPE_NAMES if k != PRODUCT_TYPE_OCEAN_PANEL
)

# Related CDO Ocean ecosystem devices (panels, chargers, monitors).
OCEAN_ECOSYSTEM_PRODUCT_TYPES = ("88", "95", "99", "105")

LOGIN_HEADERS = {
    "Content-Type": "application/json",
    "lang": "en_US",
}

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "lang": "en_US",
}

# EMS work modes from JTS1_EMS_CHANGE_REPORT.emsWordMode
EMS_WORK_MODES = {
    "WORKMODE_SELFUSE": "self_use",
    "WORKMODE_TOU": "time_of_use",
    "WORKMODE_BACKUP": "backup",
    "WORKMODE_DBG": "debug",
}

# CDO Ocean Pro MQTT field 1470 — live Self-powered ↔ Intelligent toggles
# (2026-07-20) flipped 0 ↔ 9. EU PowerOcean docs list overlapping numeric
# codes (9=TIMER_MODE there); we prefer the US app names confirmed live.
EMS_WORK_MODE_CODES = {
    0: "self_use",       # Self-powered
    1: "time_of_use",
    2: "backup",         # Emergency Backup (foxthefox) / seen in dumps
    3: "debug",
    4: "ac_makeup",
    5: "drm",
    6: "remote_schedule",
    7: "standby",
    8: "soc_calibration",
    9: "intelligent",    # Intelligent (app) — EU docs call this TIMER_MODE
}
