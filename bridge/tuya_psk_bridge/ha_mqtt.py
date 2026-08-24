"""Home Assistant MQTT discovery and state publisher.

Publishes MQTT discovery configs and state updates to Home Assistant's
MQTT broker so Tuya devices appear as native HA entities without a
custom integration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from tuya_psk_bridge.models import BridgeConfig, DecodedDps, DeviceConfig, DeviceMapping

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryPayload:
    """MQTT discovery config for a single Home Assistant entity.

    Serialized to JSON and published to the HA MQTT discovery topic.
    """

    name: str
    unique_id: str
    state_topic: str
    platform: str
    device: dict[str, Any]
    availability_topic: str
    payload_available: str
    payload_not_available: str
    payload_on: str | None = None
    payload_off: str | None = None
    device_class: str | None = None
    command_topic: str | None = None

    def to_json(self) -> str:
        """Serialize to JSON, omitting None fields so HA uses defaults."""
        data: dict[str, Any] = {
            "name": self.name,
            "unique_id": self.unique_id,
            "state_topic": self.state_topic,
            "platform": self.platform,
            "device": self.device,
            "availability_topic": self.availability_topic,
            "payload_available": self.payload_available,
            "payload_not_available": self.payload_not_available,
        }
        # Only include optional fields when they have values
        if self.device_class is not None:
            data["device_class"] = self.device_class
        if self.payload_on is not None:
            data["payload_on"] = self.payload_on
        if self.payload_off is not None:
            data["payload_off"] = self.payload_off
        if self.command_topic is not None:
            data["command_topic"] = self.command_topic
        return json.dumps(data)


def _discovery_topic(device_config: DeviceConfig, mapping: DeviceMapping) -> str:
    """Build the HA MQTT discovery config topic for a mapping."""
    return f"homeassistant/{mapping.platform}/tuya_psk_{device_config.device_id}_{mapping.dps}/config"


def _state_topic(device_config: DeviceConfig, mapping: DeviceMapping) -> str:
    """Build the HA MQTT state topic for a mapping."""
    return f"homeassistant/{mapping.platform}/tuya_psk_{device_config.device_id}_{mapping.dps}/state"


def ha_command_topic(device_config: DeviceConfig, mapping: DeviceMapping) -> str:
    """Build the HA MQTT command topic HA publishes to for a controllable entity."""
    return f"homeassistant/{mapping.platform}/tuya_psk_{device_config.device_id}_{mapping.dps}/set"


def _availability_topic(device_config: DeviceConfig) -> str:
    """Build the availability topic shared across all mappings of a device."""
    return f"homeassistant/status/tuya_psk_{device_config.device_id}/availability"


def _unique_id(device_config: DeviceConfig, mapping: DeviceMapping) -> str:
    """Build a stable unique_id for the HA entity."""
    return f"tuya_psk_{device_config.device_id}_{mapping.dps}"


def _device_info(device_config: DeviceConfig) -> dict[str, Any]:
    """Build the HA device registry entry."""
    return {
        "identifiers": [device_config.device_id],
        "manufacturer": "Tuya",
        "model": device_config.profile,
        "name": device_config.name,
    }


# Platforms Home Assistant can send commands to. Everything else is reported
# state only, and must not advertise a command topic it cannot honour.
_CONTROLLABLE_PLATFORMS = frozenset({"switch"})

# The HA-facing on/off strings. Mappings normalize raw device values to these.
_STATE_ON = "ON"
_STATE_OFF = "OFF"


def _on_off_payloads(mapping: DeviceMapping) -> tuple[str | None, str | None]:
    """Pick the on/off state strings a mapping can actually produce.

    Derived from the mapping's normalized values rather than from door-sensor
    key names, so switches (``True``/``False``) work the same as contacts
    (``open``/``closed``).
    """
    normalized = set(mapping.values.values())
    return (
        _STATE_ON if _STATE_ON in normalized else None,
        _STATE_OFF if _STATE_OFF in normalized else None,
    )


def build_discovery_payload(
    device_config: DeviceConfig,
    mapping: DeviceMapping,
) -> DiscoveryPayload:
    """Build MQTT discovery config for a device mapping.

    The resulting payload conforms to the HA MQTT discovery spec so the
    entity auto-appears in Home Assistant when the config is published.
    """
    payload_on, payload_off = _on_off_payloads(mapping)
    controllable = mapping.platform in _CONTROLLABLE_PLATFORMS

    return DiscoveryPayload(
        name=device_config.name,
        unique_id=_unique_id(device_config, mapping),
        state_topic=_state_topic(device_config, mapping),
        platform=mapping.platform,
        device=_device_info(device_config),
        availability_topic=_availability_topic(device_config),
        payload_available="online",
        payload_not_available="offline",
        payload_on=payload_on,
        payload_off=payload_off,
        device_class=mapping.device_class,
        command_topic=ha_command_topic(device_config, mapping) if controllable else None,
    )


def build_state_payload(dps: DecodedDps) -> str:
    """Map normalized_value to state string.

    Falls back to raw_value when no mapping produced a normalized value.
    """
    if dps.normalized_value is not None:
        return dps.normalized_value
    return dps.raw_value


class HaMqttPublisher:
    """Publishes HA MQTT discovery configs and device state updates.

    Uses paho-mqtt v2 API (CallbackAPIVersion.VERSION2).
    Discovery, availability, and state messages are all published with
    retain=True so HA recovers entity config and last-known value even
    after an HA or broker restart.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._connected = False
        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        if username and password:
            self._client.username_pw_set(username, password)
        self._client.enable_logger(logger)

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected to the MQTT broker."""
        return self._connected

    def connect(self) -> None:
        """Connect to the HA MQTT broker and start the network loop."""
        logger.info("Connecting to HA MQTT broker at %s:%d", self._host, self._port)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.connect(self._host, self._port)
        self._client.loop_start()

    def disconnect(self) -> None:
        """Stop the network loop and disconnect cleanly."""
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False
        logger.info("Disconnected from HA MQTT broker")

    def publish_discovery(self, device_config: DeviceConfig) -> None:
        """Publish retained discovery config for all mappings of a device."""
        for mapping in device_config.mappings:
            payload = build_discovery_payload(device_config, mapping)
            topic = _discovery_topic(device_config, mapping)
            self._client.publish(
                topic,
                payload.to_json(),
                qos=1,
                retain=True,
            )
            logger.debug(
                "Published discovery for %s mapping dps=%s",
                device_config.device_id,
                mapping.dps,
            )

    def publish_state(
        self,
        device_config: DeviceConfig,
        dps: DecodedDps,
    ) -> None:
        """Publish a retained state update for a single DPS.

        State is retained so Home Assistant restores the last known value
        after an HA or broker restart — devices like door sensors only
        publish on change, so a non-retained topic would leave the entity
        ``unknown`` until the next state transition.
        """
        mapping = self._find_mapping(device_config, dps.dps_id)
        if mapping is None:
            logger.debug(
                "No mapping for dps=%s on device %s, skipping state publish",
                dps.dps_id,
                device_config.device_id,
            )
            return
        topic = _state_topic(device_config, mapping)
        state = build_state_payload(dps)
        self._client.publish(topic, state, qos=0, retain=True)

    def publish_availability(self, device_config: DeviceConfig, available: bool) -> None:
        """Publish device availability to the shared availability topic."""
        topic = _availability_topic(device_config)
        payload = "online" if available else "offline"
        self._client.publish(topic, payload, qos=1, retain=True)

    def subscribe_commands(
        self,
        config: BridgeConfig,
        callback: Callable[[str, str], None],
    ) -> None:
        """Subscribe to the command topic of every controllable mapping.

        Args:
            config: Bridge configuration to walk for controllable mappings.
            callback: Called with ``(topic, payload)`` for each HA command.
        """
        def _on_message(_client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
            callback(message.topic, message.payload.decode("utf-8", errors="replace"))

        count = 0
        for device_config in config.devices:
            for mapping in device_config.mappings:
                if mapping.platform not in _CONTROLLABLE_PLATFORMS:
                    continue
                topic = ha_command_topic(device_config, mapping)
                self._client.subscribe(topic, qos=1)
                self._client.message_callback_add(topic, _on_message)
                count += 1
        logger.info("Subscribed to %d HA command topic(s)", count)

    def publish_all_discovery(self, config: BridgeConfig) -> None:
        """Publish discovery configs for every configured device."""
        for device_config in config.devices:
            self.publish_discovery(device_config)
        logger.info("Published discovery for %d devices", len(config.devices))

    @staticmethod
    def _find_mapping(
        device_config: DeviceConfig,
        dps_id: str,
    ) -> DeviceMapping | None:
        """Look up the DeviceMapping for a given DPS ID, or None."""
        for mapping in device_config.mappings:
            if mapping.dps == dps_id:
                return mapping
        return None

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: dict,
        _reason_code: Any,
        _properties: Any,
    ) -> None:
        self._connected = True
        logger.info("Connected to HA MQTT broker")

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _disconnect_flags: Any,
        _reason_code: Any,
        _properties: Any,
    ) -> None:
        self._connected = False
        logger.warning("Disconnected from HA MQTT broker")
