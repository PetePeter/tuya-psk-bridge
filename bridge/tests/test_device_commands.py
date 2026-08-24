"""Tests for the outbound command path — bridge to device.

Until now the bridge only ever read from devices.  Controlling the GIEX water
timer means publishing a Tuya command back down the same TLS-PSK session, so
these tests cover encoding, framing, topic naming and dispatch.
"""

from __future__ import annotations

import json

import pytest

from tuya_psk_bridge.commands import command_topic, encode_command
from tuya_psk_bridge.models import BridgeConfig, DeviceConfig, DeviceMapping
from tuya_psk_bridge.mqtt_decoder import decode_mqtt_payload
from tuya_psk_bridge.server import PskMqttServer, _parse_mqtt_publish

_LOCAL_KEY = "TESTKEY012345678"  # any 16 chars; not a real device key
_DEVICE_ID = "bfa1de1cfc890dea68i4qh"


class _RecordingSocket:
    """A stand-in for the TLS socket that keeps what was written."""

    def __init__(self) -> None:
        self.written = bytearray()

    def sendall(self, data: bytes) -> None:
        self.written.extend(data)


class TestEncodeCommand:
    def test_round_trips_through_the_existing_decoder(self):
        """What we encrypt must be exactly what the decoder reads back."""
        raw = encode_command(_DEVICE_ID, {"1": True}, _LOCAL_KEY)

        event = decode_mqtt_payload(raw, _LOCAL_KEY)

        assert [(d.dps_id, d.raw_value) for d in event.dps_list] == [("1", "True")]

    def test_envelope_is_the_tuya_2_2_header(self):
        raw = encode_command(_DEVICE_ID, {"1": True}, _LOCAL_KEY)

        assert raw[:3] == b"2.2"
        assert len(raw) > 15

    def test_payload_carries_the_device_id(self):
        from tuya_psk_bridge.mqtt_decoder import decrypt_payload, strip_envelope

        _, ciphertext = strip_envelope(encode_command(_DEVICE_ID, {"1": True}, _LOCAL_KEY))
        data = json.loads(decrypt_payload(ciphertext, _LOCAL_KEY))

        assert data["data"]["devId"] == _DEVICE_ID


class TestCommandTopic:
    def test_commands_go_to_the_device_in_topic(self):
        assert command_topic(_DEVICE_ID) == f"smart/device/in/{_DEVICE_ID}"


class TestSendCommand:
    def _server(self, session_device_id: str | None):
        config = BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=8883,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            devices=[
                DeviceConfig(
                    device_id=_DEVICE_ID,
                    local_key=_LOCAL_KEY,
                    name="GIEX Water Timer",
                    profile="water_timer",
                )
            ],
        )
        server = PskMqttServer(
            config=config,
            psk_hint=b"hint",
            event_callback=lambda device_id, event: None,
        )
        sock = _RecordingSocket()
        if session_device_id is not None:
            session = object.__new__(_SessionShim)
            session.sock = sock
            session.device_id = session_device_id
            server._sessions.append(session)
        return server, sock

    def test_publishes_a_well_formed_mqtt_packet(self):
        """Frame it, then parse it back with the bridge's own parser."""
        server, sock = self._server(_DEVICE_ID)

        assert server.send_command(_DEVICE_ID, {"1": True}) is True

        topic, payload = _parse_mqtt_publish(bytes(sock.written))
        assert topic == f"smart/device/in/{_DEVICE_ID}"
        event = decode_mqtt_payload(payload, _LOCAL_KEY)
        assert [(d.dps_id, d.raw_value) for d in event.dps_list] == [("1", "True")]

    def test_unconnected_device_is_a_no_op(self):
        """No session for that device — warn and return False, never raise."""
        server, sock = self._server(None)

        assert server.send_command(_DEVICE_ID, {"1": True}) is False
        assert bytes(sock.written) == b""


class _SessionShim:
    """Minimal session object with the attributes send_command touches."""

    __slots__ = ("sock", "device_id", "buffer", "client_id")


class TestHaCommandDispatch:
    """An HA command topic message reaches the right device and DPS."""

    def _runtime(self):
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime

        config = BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=8883,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            devices=[
                DeviceConfig(
                    device_id=_DEVICE_ID,
                    local_key=_LOCAL_KEY,
                    name="GIEX Water Timer",
                    profile="water_timer",
                    mappings=[
                        DeviceMapping(
                            dps="1",
                            platform="switch",
                            values={"True": "ON", "False": "OFF"},
                        )
                    ],
                )
            ],
        )
        return BridgeRuntime(config=config, psk_hint=b"hint")

    def test_on_command_maps_back_to_the_raw_tuya_value(self):
        runtime = self._runtime()
        sent: list[tuple[str, dict]] = []
        runtime.send_command = lambda device_id, dps: sent.append((device_id, dps)) or True

        runtime.handle_ha_command(
            f"homeassistant/switch/tuya_psk_{_DEVICE_ID}_1/set", "ON"
        )

        assert sent == [(_DEVICE_ID, {"1": True})]

    def test_off_command_maps_back_to_the_raw_tuya_value(self):
        runtime = self._runtime()
        sent: list[tuple[str, dict]] = []
        runtime.send_command = lambda device_id, dps: sent.append((device_id, dps)) or True

        runtime.handle_ha_command(
            f"homeassistant/switch/tuya_psk_{_DEVICE_ID}_1/set", "OFF"
        )

        assert sent == [(_DEVICE_ID, {"1": False})]

    def test_unknown_topic_is_ignored(self):
        runtime = self._runtime()
        sent: list[tuple[str, dict]] = []
        runtime.send_command = lambda device_id, dps: sent.append((device_id, dps)) or True

        runtime.handle_ha_command("homeassistant/switch/tuya_psk_nosuchdevice_1/set", "ON")

        assert sent == []
