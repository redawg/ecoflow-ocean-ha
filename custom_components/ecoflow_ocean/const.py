"""Constants for the EcoFlow Power Ocean integration."""

from .pyecoflowocean.const import (
    PRODUCT_TYPE_NAMES,
    PRODUCT_TYPE_POWER_OCEAN,
)

DOMAIN = "ecoflow_ocean"

CONF_SERIAL_NUMBER = "serial_number"
CONF_PRODUCT_TYPE = "product_type"
CONF_REGION = "region"

DEFAULT_SCAN_INTERVAL = 30
DEFAULT_REGION = "us"
DEFAULT_PRODUCT_TYPE = PRODUCT_TYPE_POWER_OCEAN

PRODUCT_TYPE_OPTIONS = PRODUCT_TYPE_NAMES

MANUFACTURER = "EcoFlow"
MODEL_POWER_OCEAN = "Power Ocean"
