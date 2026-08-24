"""Data models for Tuya PSK bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DecodedDps:
    """A single decoded data point from a Tuya device.

    Attributes:
        dps_id: The DPS identifier string (e.g. "1", "101").
        raw_value: The raw string value from the device.
        normalized_value: Mapped value if a mapping exists, otherwise None.
    """

    dps_id: str
    raw_value: str
    normalized_value: str | None = None


@dataclass(frozen=True)
class DecodedEvent:
    """A fully decoded event from a Tuya MQTT payload.

    Attributes:
        device_id: Tuya device identifier.
        protocol: Protocol version number.
        dps_list: Decoded data points extracted from the payload.
        timestamp: When the event was received (None if unavailable).
    """

    device_id: str
    protocol: int
    dps_list: list[DecodedDps] = field(default_factory=list)
    timestamp: datetime | None = None


@dataclass(frozen=True)
class DeviceMapping:
    """Maps a Tuya DPS ID to a Home Assistant entity definition.

    Attributes:
        dps: The DPS identifier on the Tuya device.
        platform: HA platform type (e.g. "binary_sensor").
        device_class: Optional HA device class (e.g. "door").
        values: Mapping from Tuya raw values to normalized states
                (e.g. {"open": "ON", "closed": "OFF"}).
    """

    dps: str
    platform: str
    device_class: str | None = None
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class DeviceConfig:
    """Configuration for a single Tuya device.

    Attributes:
        device_id: Tuya device identifier.
        local_key: The 16-byte local key for AES decryption.
        name: Human-readable device name.
        profile: Device profile name (e.g. "door_sensor").
        mappings: List of DPS-to-HA mappings for this device.
    """

    device_id: str
    local_key: str
    name: str
    profile: str
    mappings: list[DeviceMapping] = field(default_factory=list)


@dataclass
class BridgeConfig:
    """Top-level bridge configuration.

    Attributes:
        listen_host: Host address for the Tuya PSK listener.
        mqtt_psk_port: Primary port for the Tuya PSK MQTT broker.
        additional_psk_ports: Further ports to listen on. Devices do not fall
            back between endpoints, so each device family must be met on the
            port it already uses (door sensors 8886, GIEX water timer 8883).
        psk_variant: Lab-only PSK derivation applied to the additional ports.
            The primary port always uses the proven derivation, so experiments
            cannot affect devices that already work.
        ha_mqtt_host: Home Assistant MQTT broker host.
        ha_mqtt_port: Home Assistant MQTT broker port.
        ha_mqtt_username: Optional HA MQTT username.
        ha_mqtt_password: Optional HA MQTT password.
        log_level: Logging level string (e.g. "INFO", "DEBUG").
        devices: List of configured device entries.
    """

    listen_host: str
    mqtt_psk_port: int
    ha_mqtt_host: str
    ha_mqtt_port: int
    ha_mqtt_username: str | None
    ha_mqtt_password: str | None
    log_level: str
    devices: list[DeviceConfig] = field(default_factory=list)
    additional_psk_ports: list[int] = field(default_factory=list)
    psk_variant: str | None = None

    @property
    def listen_ports(self) -> list[int]:
        """Every port to bind, primary first, deduplicated in order."""
        return list(dict.fromkeys([self.mqtt_psk_port, *self.additional_psk_ports]))
