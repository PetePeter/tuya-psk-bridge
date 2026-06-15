"""Constants for the Tuya PSK Bridge integration."""

DOMAIN = "tuya_psk_bridge"

CONF_DEVICES = "devices"
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_NAME = "name"
CONF_PROFILE = "profile"
CONF_MAPPINGS = "mappings"
CONF_DPS = "dps"
CONF_PLATFORM = "platform"
CONF_DEVICE_CLASS = "device_class"
CONF_VALUES = "values"

DEFAULT_PLATFORM = "binary_sensor"
DEFAULT_DEVICE_CLASS_DOOR = "door"

# Known device profiles
DOOR_SENSOR = "door_sensor"

PROFILES = [DOOR_SENSOR]

# Fields containing secrets that must be redacted in diagnostics/logs
SENSITIVE_FIELDS = {"local_key"}

# Door sensor default DPS mapping
DOOR_SENSOR_DEFAULTS = {
    CONF_DPS: "1",
    CONF_PLATFORM: "binary_sensor",
    CONF_DEVICE_CLASS: "door",
    CONF_VALUES: {"open": "ON", "closed": "OFF"},
}
