"""Tests for server configuration — BridgeConfig construction and validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tuya_psk_bridge.models import BridgeConfig, DeviceConfig, DeviceMapping
from tuya_psk_bridge.server import (
    PskMqttServer,
    _parse_mqtt_connect,
    _parse_mqtt_subscribe,
)


# ---------------------------------------------------------------------------
# BridgeConfig
# ---------------------------------------------------------------------------

class TestBridgeConfig:
    """BridgeConfig is a plain dataclass — test required fields and defaults."""

    def test_minimal_valid_config(self):
        """BridgeConfig with all required fields should construct."""
        config = BridgeConfig(
            listen_host="0.0.0.0",
            mqtt_psk_port=8886,
            ha_mqtt_host="192.168.1.10",
            ha_mqtt_port=1883,
            ha_mqtt_username="homeassistant",
            ha_mqtt_password="supersecret",
            log_level="INFO",
        )
        assert config.listen_host == "0.0.0.0"
        assert config.mqtt_psk_port == 8886
        assert config.ha_mqtt_host == "192.168.1.10"
        assert config.ha_mqtt_port == 1883
        assert config.ha_mqtt_username == "homeassistant"
        assert config.ha_mqtt_password == "supersecret"
        assert config.log_level == "INFO"
        assert config.devices == []

    def test_default_devices_is_empty_list(self):
        """When devices is not passed, it defaults to an empty list."""
        config = BridgeConfig(
            listen_host="0.0.0.0",
            mqtt_psk_port=8886,
            ha_mqtt_host="192.168.1.10",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="WARNING",
        )
        assert config.devices == []

    def test_none_credentials_allowed(self):
        """HA MQTT credentials are optional — None is valid."""
        config = BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=8886,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="DEBUG",
        )
        assert config.ha_mqtt_username is None
        assert config.ha_mqtt_password is None

    def test_config_with_devices(self):
        """BridgeConfig should accept a list of DeviceConfig objects."""
        device = DeviceConfig(
            device_id="1234567890abcdef",
            local_key="abcdef0123456789",
            name="Front Door",
            profile="door_sensor",
        )
        config = BridgeConfig(
            listen_host="0.0.0.0",
            mqtt_psk_port=8886,
            ha_mqtt_host="192.168.1.10",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            devices=[device],
        )
        assert len(config.devices) == 1
        assert config.devices[0].device_id == "1234567890abcdef"

    def test_mutable_dataclass(self):
        """BridgeConfig is mutable — fields can be updated after creation."""
        config = BridgeConfig(
            listen_host="0.0.0.0",
            mqtt_psk_port=8886,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
        )
        config.listen_host = "1.2.3.4"
        assert config.listen_host == "1.2.3.4"


# ---------------------------------------------------------------------------
# DeviceConfig
# ---------------------------------------------------------------------------

class TestDeviceConfig:
    def test_minimal_device_config(self):
        device = DeviceConfig(
            device_id="abc123",
            local_key="key1234567890123",
            name="Test Sensor",
            profile="generic",
        )
        assert device.device_id == "abc123"
        assert device.local_key == "key1234567890123"
        assert device.mappings == []

    def test_device_with_mappings(self):
        mapping = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            device_class="door",
            values={"open": "ON", "closed": "OFF"},
        )
        device = DeviceConfig(
            device_id="abc123",
            local_key="key1234567890123",
            name="Door",
            profile="door_sensor",
            mappings=[mapping],
        )
        assert len(device.mappings) == 1
        assert device.mappings[0].dps == "1"

    def test_mutable_mappings(self):
        """DeviceConfig is mutable — mappings can be appended."""
        device = DeviceConfig(
            device_id="abc",
            local_key="key",
            name="Dev",
            profile="generic",
        )
        device.mappings.append(
            DeviceMapping(dps="2", platform="sensor")
        )
        assert len(device.mappings) == 1


class TestMqttPacketDispatch:
    def test_connect_parser_uses_fixed_header_length(self):
        packet = (
            b"\x10\x20"
            b"\x00\x04MQTT"
            b"\x04"
            b"\x02"
            b"\x00\x3c"
            b"\x00\x14"
            b"0123456789abcdefabcd"
        )

        assert _parse_mqtt_connect(packet) == "0123456789abcdefabcd"

    def test_subscribe_parser_uses_packet_id_and_fixed_header_length(self):
        packet = (
            b"\x82\x25"
            b"\x12\x34"
            b"\x00\x24"
            b"smart/device/in/0123456789abcdefabcd"
            b"\x00"
        )

        assert _parse_mqtt_subscribe(packet) == (
            0x1234,
            "smart/device/in/0123456789abcdefabcd",
        )

    def test_subscribe_fixed_header_dispatches_after_type_mask(self):
        """SUBSCRIBE is 0x82 on the wire, but dispatch masks it to 0x80."""
        server = object.__new__(PskMqttServer)
        session = SimpleNamespace(buffer=bytearray([0x82, 0x00]))
        called = []

        def handle_subscribe(received_session):
            called.append(received_session)

        server._handle_subscribe = handle_subscribe

        PskMqttServer._process_buffer(server, session)

        assert called == [session]
        assert session.buffer == bytearray()


# ---------------------------------------------------------------------------
# Session pruning — regression for the 2026-11-02 socket leak
# ---------------------------------------------------------------------------

class TestPruneDeadSessions:
    """Battery sensors sleep without a FIN; idle sessions must be closed or
    their sockets leak until select() exceeds FD_SETSIZE and the loop dies."""

    def _server_with_sessions(self, count):
        import socket

        from tuya_psk_bridge.server import _DeviceSession

        server = object.__new__(PskMqttServer)
        server._sessions = []
        peers = []
        for _ in range(count):
            ours, theirs = socket.socketpair()
            server._sessions.append(_DeviceSession(ours, ("10.98.1.1", 1)))
            peers.append(theirs)
        return server, peers

    def test_idle_session_is_closed(self):
        from tuya_psk_bridge.server import SESSION_IDLE_TIMEOUT_S

        server, peers = self._server_with_sessions(2)
        idle, fresh = server._sessions
        idle.last_activity -= SESSION_IDLE_TIMEOUT_S + 1

        server._prune_dead_sessions()

        assert server._sessions == [fresh]
        assert idle.sock.fileno() == -1
        assert fresh.sock.fileno() != -1
        for s in [fresh.sock, *peers]:
            s.close()

    def test_active_session_survives(self):
        server, peers = self._server_with_sessions(1)

        server._prune_dead_sessions()

        assert len(server._sessions) == 1
        for s in [server._sessions[0].sock, *peers]:
            s.close()
