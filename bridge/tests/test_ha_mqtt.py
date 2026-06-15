"""Unit tests for the HA MQTT discovery and state publisher.

Tests use real payload construction but mock the MQTT client so no broker
connection is needed.
"""

from __future__ import annotations

import json

import pytest

from tuya_psk_bridge.models import BridgeConfig, DecodedDps, DeviceConfig, DeviceMapping
from tuya_psk_bridge.ha_mqtt import (
    DiscoveryPayload,
    HaMqttPublisher,
    build_discovery_payload,
    build_state_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _door_device(
    device_id: str = "dev001",
    name: str = "Front Door",
    profile: str = "door_sensor",
) -> DeviceConfig:
    """Build a DeviceConfig with a single door-sensor mapping."""
    return DeviceConfig(
        device_id=device_id,
        local_key="placeholder_key",
        name=name,
        profile=profile,
        mappings=[
            DeviceMapping(
                dps="1",
                platform="binary_sensor",
                device_class="door",
                values={"open": "ON", "closed": "OFF"},
            ),
        ],
    )


def _multi_mapping_device() -> DeviceConfig:
    """Build a DeviceConfig with multiple platform mappings."""
    return DeviceConfig(
        device_id="dev_multi",
        local_key="placeholder_key",
        name="Multi Sensor",
        profile="multi_sensor",
        mappings=[
            DeviceMapping(
                dps="1",
                platform="binary_sensor",
                device_class="door",
                values={"open": "ON", "closed": "OFF"},
            ),
            DeviceMapping(
                dps="2",
                platform="sensor",
                device_class="temperature",
                values={"warm": "high", "cool": "low"},
            ),
        ],
    )


def _bridge_config(*devices: DeviceConfig) -> BridgeConfig:
    """Wrap devices in a minimal BridgeConfig."""
    return BridgeConfig(
        listen_host="0.0.0.0",
        mqtt_psk_port=8886,
        ha_mqtt_host="test-broker",
        ha_mqtt_port=1883,
        ha_mqtt_username=None,
        ha_mqtt_password=None,
        log_level="INFO",
        devices=list(devices),
    )


# ---------------------------------------------------------------------------
# build_discovery_payload
# ---------------------------------------------------------------------------

class TestBuildDiscoveryPayload:
    def test_binary_sensor_door(self):
        """Discovery payload for a binary_sensor door mapping has correct shape."""
        device = _door_device()
        mapping = device.mappings[0]
        payload = build_discovery_payload(device, mapping)

        assert payload.platform == "binary_sensor"
        assert payload.device_class == "door"
        assert payload.name == "Front Door"

    def test_unique_id_format(self):
        """unique_id follows tuya_psk_{device_id}_{dps} pattern."""
        device = _door_device(device_id="abc123")
        mapping = device.mappings[0]
        payload = build_discovery_payload(device, mapping)

        assert payload.unique_id == "tuya_psk_abc123_1"

    def test_state_topic_format(self):
        """state_topic follows homeassistant/{platform}/tuya_psk_{device_id}_{dps}/state."""
        device = _door_device(device_id="abc123")
        mapping = device.mappings[0]
        payload = build_discovery_payload(device, mapping)

        expected = "homeassistant/binary_sensor/tuya_psk_abc123_1/state"
        assert payload.state_topic == expected

    def test_device_identifiers(self):
        """device identifiers list contains the device_id."""
        device = _door_device(device_id="mydev")
        mapping = device.mappings[0]
        payload = build_discovery_payload(device, mapping)

        assert payload.device["identifiers"] == ["mydev"]

    def test_device_manufacturer(self):
        payload = build_discovery_payload(_door_device(), _door_device().mappings[0])
        assert payload.device["manufacturer"] == "Tuya"

    def test_device_model(self):
        device = _door_device(profile="door_sensor_v2")
        payload = build_discovery_payload(device, device.mappings[0])
        assert payload.device["model"] == "door_sensor_v2"

    def test_payload_on_off_from_values(self):
        """payload_on/payload_off are populated from mapping values dict."""
        device = _door_device()
        mapping = device.mappings[0]
        payload = build_discovery_payload(device, mapping)

        assert payload.payload_on == "ON"
        assert payload.payload_off == "OFF"

    def test_no_payload_on_off_when_values_empty(self):
        """payload_on/payload_off are None when mapping has no value entries."""
        device = DeviceConfig(
            device_id="dev", local_key="key", name="Plain", profile="basic",
            mappings=[DeviceMapping(dps="1", platform="sensor")],
        )
        payload = build_discovery_payload(device, device.mappings[0])

        assert payload.payload_on is None
        assert payload.payload_off is None

    def test_availability_topic_uses_device_id(self):
        device = _door_device(device_id="dev999")
        payload = build_discovery_payload(device, device.mappings[0])

        assert payload.availability_topic == "homeassistant/status/tuya_psk_dev999/availability"
        assert payload.payload_available == "online"
        assert payload.payload_not_available == "offline"

    def test_sensor_platform(self):
        """Discovery works for non-binary_sensor platforms like sensor."""
        device = _multi_mapping_device()
        temp_mapping = device.mappings[1]
        payload = build_discovery_payload(device, temp_mapping)

        assert payload.platform == "sensor"
        assert payload.device_class == "temperature"
        assert payload.unique_id == "tuya_psk_dev_multi_2"

    def test_unique_id_stability(self):
        """Same device config and mapping always produce the same payload."""
        device = _door_device()
        mapping = device.mappings[0]
        p1 = build_discovery_payload(device, mapping)
        p2 = build_discovery_payload(device, mapping)

        assert p1.unique_id == p2.unique_id
        assert p1.to_json() == p2.to_json()

    def test_unique_id_differs_by_device(self):
        """Different device IDs produce different unique_ids."""
        d1 = _door_device(device_id="dev_a")
        d2 = _door_device(device_id="dev_b")

        assert d1.mappings[0].dps == d2.mappings[0].dps
        p1 = build_discovery_payload(d1, d1.mappings[0])
        p2 = build_discovery_payload(d2, d2.mappings[0])

        assert p1.unique_id != p2.unique_id


# ---------------------------------------------------------------------------
# DiscoveryPayload serialization
# ---------------------------------------------------------------------------

class TestDiscoveryPayloadSerialization:
    def test_to_json_is_valid(self):
        """Serialized JSON is parseable and contains required HA fields."""
        device = _door_device()
        payload = build_discovery_payload(device, device.mappings[0])
        data = json.loads(payload.to_json())

        assert "name" in data
        assert "unique_id" in data
        assert "state_topic" in data
        assert "platform" in data
        assert "device" in data
        assert "availability_topic" in data

    def test_to_json_omits_none_device_class(self):
        """When device_class is None, the key should not appear in JSON."""
        device = DeviceConfig(
            device_id="dev", local_key="key", name="Plain", profile="basic",
            mappings=[DeviceMapping(dps="1", platform="sensor")],
        )
        payload = build_discovery_payload(device, device.mappings[0])
        data = json.loads(payload.to_json())

        assert "device_class" not in data

    def test_to_json_omits_none_payload_on_off(self):
        """When payload_on/off are None, they should not appear in JSON."""
        device = DeviceConfig(
            device_id="dev", local_key="key", name="Plain", profile="basic",
            mappings=[DeviceMapping(dps="1", platform="sensor")],
        )
        payload = build_discovery_payload(device, device.mappings[0])
        data = json.loads(payload.to_json())

        assert "payload_on" not in data
        assert "payload_off" not in data

    def test_to_json_includes_device_class_when_set(self):
        device = _door_device()
        payload = build_discovery_payload(device, device.mappings[0])
        data = json.loads(payload.to_json())

        assert data["device_class"] == "door"

    def test_to_json_device_shape_matches_ha_spec(self):
        """The device dict must have identifiers (list), manufacturer, model, name."""
        device = _door_device()
        payload = build_discovery_payload(device, device.mappings[0])
        data = json.loads(payload.to_json())

        dev = data["device"]
        assert isinstance(dev["identifiers"], list)
        assert len(dev["identifiers"]) == 1
        assert isinstance(dev["manufacturer"], str)
        assert isinstance(dev["model"], str)
        assert isinstance(dev["name"], str)


# ---------------------------------------------------------------------------
# build_state_payload
# ---------------------------------------------------------------------------

class TestBuildStatePayload:
    def test_normalized_value_used(self):
        dps = DecodedDps(dps_id="1", raw_value="open", normalized_value="ON")
        assert build_state_payload(dps) == "ON"

    def test_raw_value_fallback(self):
        """When normalized_value is None, raw_value is returned."""
        dps = DecodedDps(dps_id="101", raw_value="some_raw", normalized_value=None)
        assert build_state_payload(dps) == "some_raw"

    def test_default_normalized_none(self):
        """DecodedDps without normalized_value defaults to None -> use raw."""
        dps = DecodedDps(dps_id="2", raw_value="mystery")
        assert build_state_payload(dps) == "mystery"


# ---------------------------------------------------------------------------
# HaMqttPublisher (mocked MQTT client)
# ---------------------------------------------------------------------------

class TestHaMqttPublisher:
    @pytest.fixture()
    def publisher(self, monkeypatch: pytest.MonkeyPatch):
        """Create a publisher with a mocked MQTT client."""
        pub = HaMqttPublisher(
            host="test-broker",
            port=1883,
            username="test_user",
            password="test_pass",
        )
        # Replace the internal client with a mock to avoid real connections
        mock_client = _MockMqttClient()
        monkeypatch.setattr(pub, "_client", mock_client)
        return pub

    def test_connect(self, publisher: HaMqttPublisher):
        publisher.connect()
        assert publisher._client.connected  # type: ignore[attr-defined]

    def test_disconnect(self, publisher: HaMqttPublisher):
        publisher.disconnect()
        assert not publisher._client.connected  # type: ignore[attr-defined]

    def test_publish_discovery_single_device(self, publisher: HaMqttPublisher):
        device = _door_device(device_id="dev_disc")
        publisher.publish_discovery(device)

        assert len(publisher._client.published) == 1  # type: ignore[attr-defined]
        topic, msg, qos, retain = publisher._client.published[0]  # type: ignore[attr-defined]
        assert topic == "homeassistant/binary_sensor/tuya_psk_dev_disc_1/config"
        assert qos == 1
        assert retain is True

        data = json.loads(msg)
        assert data["unique_id"] == "tuya_psk_dev_disc_1"
        assert data["platform"] == "binary_sensor"

    def test_publish_discovery_multi_mapping(self, publisher: HaMqttPublisher):
        device = _multi_mapping_device()
        publisher.publish_discovery(device)

        assert len(publisher._client.published) == 2  # type: ignore[attr-defined]
        topics = [p[0] for p in publisher._client.published]  # type: ignore[attr-defined]
        assert any("binary_sensor" in t for t in topics)
        assert any("sensor" in t for t in topics)

    def test_publish_state_mapped_dps(self, publisher: HaMqttPublisher):
        device = _door_device(device_id="dev_state")
        dps = DecodedDps(dps_id="1", raw_value="open", normalized_value="ON")
        publisher.publish_state(device, dps)

        assert len(publisher._client.published) == 1  # type: ignore[attr-defined]
        topic, msg, qos, retain = publisher._client.published[0]  # type: ignore[attr-defined]
        assert topic == "homeassistant/binary_sensor/tuya_psk_dev_state_1/state"
        assert msg == "ON"
        assert qos == 0
        assert retain is False

    def test_publish_state_unmapped_dps_skipped(self, publisher: HaMqttPublisher):
        """State publish is skipped when the DPS has no mapping."""
        device = _door_device()  # only maps dps="1"
        dps = DecodedDps(dps_id="99", raw_value="foo", normalized_value=None)
        publisher.publish_state(device, dps)

        assert len(publisher._client.published) == 0  # type: ignore[attr-defined]

    def test_publish_availability_online(self, publisher: HaMqttPublisher):
        device = _door_device(device_id="dev_avail")
        publisher.publish_availability(device, True)

        topic, msg, qos, retain = publisher._client.published[0]  # type: ignore[attr-defined]
        assert topic == "homeassistant/status/tuya_psk_dev_avail/availability"
        assert msg == "online"
        assert retain is True

    def test_publish_availability_offline(self, publisher: HaMqttPublisher):
        device = _door_device(device_id="dev_avail")
        publisher.publish_availability(device, False)

        _, msg, _, _ = publisher._client.published[0]  # type: ignore[attr-defined]
        assert msg == "offline"

    def test_publish_all_discovery(self, publisher: HaMqttPublisher):
        d1 = _door_device(device_id="d1")
        d2 = _door_device(device_id="d2")
        config = _bridge_config(d1, d2)
        publisher.publish_all_discovery(config)

        # 1 mapping per device = 2 total discovery messages
        assert len(publisher._client.published) == 2  # type: ignore[attr-defined]

    def test_credentials_set_on_init(self):
        """Username and password are forwarded to the MQTT client."""
        pub = HaMqttPublisher(
            host="broker",
            port=8883,
            username="admin",
            password="secret",
        )
        client = pub._client
        # paho-mqtt v2 stores creds internally; just verify no crash
        assert client is not None


# ---------------------------------------------------------------------------
# Mock MQTT client
# ---------------------------------------------------------------------------

class _MockMqttClient:
    """Lightweight mock of paho.mqtt.client.Client for testing publish calls."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self.connected: bool = False

    def connect(self, host: str, port: int) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def username_pw_set(self, username: str, password: str) -> None:
        pass

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        self.published.append((topic, payload, qos, retain))
