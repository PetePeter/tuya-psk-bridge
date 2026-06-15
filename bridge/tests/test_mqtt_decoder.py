"""Unit tests for the Tuya MQTT payload decoder.

Tests use in-memory AES encryption to build deterministic fixtures — no real
device payloads, no captured traffic, no secrets.
"""

from __future__ import annotations

import json
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad

import pytest

from tuya_psk_bridge.models import DecodedDps, DeviceMapping
from tuya_psk_bridge.mqtt_decoder import (
    decrypt_payload,
    decode_mqtt_payload,
    extract_dps,
    parse_decoded_json,
    strip_envelope,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

TEST_KEY = "abcdef0123456789"  # exactly 16 bytes
_WRONG_KEY = "0000000000000000"

_ENVELOPE_HEADER = b"2.2" + b"\x00" * 12  # 15 bytes


def _build_raw_payload(json_data: dict | list, key: str = TEST_KEY) -> bytes:
    """Encrypt a JSON blob with AES-128-ECB + PKCS7 and prepend the 2.2 envelope.

    This is the inverse of the decode pipeline and is used to create
    deterministic test fixtures without any captured traffic.
    """
    plaintext = json.dumps(json_data, separators=(",", ":")).encode("utf-8")
    key_bytes = key.encode("utf-8")[:16]
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return _ENVELOPE_HEADER + ciphertext


# ---------------------------------------------------------------------------
# strip_envelope
# ---------------------------------------------------------------------------

class TestStripEnvelope:
    def test_valid_2_2_payload(self):
        payload = _ENVELOPE_HEADER + b"\x00" * 16
        protocol, ciphertext = strip_envelope(payload)
        assert protocol == "2.2"
        assert ciphertext == b"\x00" * 16

    def test_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            strip_envelope(b"2.2" + b"\x00" * 5)  # only 8 bytes

    def test_wrong_protocol_marker(self):
        bad_header = b"3.0" + b"\x00" * 12 + b"\x00" * 16
        with pytest.raises(ValueError, match="Unsupported protocol marker"):
            strip_envelope(bad_header)

    def test_empty_after_header_raises(self):
        """Payload exactly 15 bytes (header only, no ciphertext) is too short."""
        with pytest.raises(ValueError, match="too short"):
            strip_envelope(_ENVELOPE_HEADER)


# ---------------------------------------------------------------------------
# decrypt_payload
# ---------------------------------------------------------------------------

class TestDecryptPayload:
    def test_valid_encryption(self):
        raw = _build_raw_payload({"dps": {"1": "open"}})
        _, ciphertext = strip_envelope(raw)
        plaintext = decrypt_payload(ciphertext, TEST_KEY)
        assert json.loads(plaintext) == {"dps": {"1": "open"}}

    def test_wrong_key_raises(self):
        raw = _build_raw_payload({"dps": {"1": "open"}})
        _, ciphertext = strip_envelope(raw)
        with pytest.raises(ValueError, match="AES decryption failed"):
            decrypt_payload(ciphertext, _WRONG_KEY)

    def test_bad_padding_raises(self):
        """Random bytes will almost certainly have invalid PKCS7 padding."""
        with pytest.raises(ValueError, match="AES decryption failed"):
            decrypt_payload(b"\xff" * 32, TEST_KEY)

    def test_key_truncated_to_16_bytes(self):
        """Keys longer than 16 bytes should be silently truncated."""
        long_key = "a" * 32
        raw = _build_raw_payload({"test": True}, key=long_key[:16])
        _, ciphertext = strip_envelope(raw)
        # Decrypting with the truncated key should succeed
        plaintext = decrypt_payload(ciphertext, long_key)
        assert json.loads(plaintext) == {"test": True}


# ---------------------------------------------------------------------------
# parse_decoded_json
# ---------------------------------------------------------------------------

class TestParseDecodedJson:
    def test_valid_json_object(self):
        data = parse_decoded_json(b'{"dps": {"1": "ON"}}')
        assert data == {"dps": {"1": "ON"}}

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_decoded_json(b"{not valid json!!!}")

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="Expected JSON object"):
            parse_decoded_json(b'"just a string"')

    def test_empty_object(self):
        data = parse_decoded_json(b"{}")
        assert data == {}


# ---------------------------------------------------------------------------
# extract_dps
# ---------------------------------------------------------------------------

class TestExtractDps:
    def test_known_dps_open_closed(self):
        """DPS 1 with mapping should normalize 'open' -> 'ON', 'closed' -> 'OFF'."""
        mapping = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            device_class="door",
            values={"open": "ON", "closed": "OFF"},
        )
        data = {"dps": {"1": "open", "2": "closed"}}
        result = extract_dps(data, {"1": mapping})
        assert len(result) == 2
        assert result[0] == DecodedDps(dps_id="1", raw_value="open", normalized_value="ON")
        assert result[1] == DecodedDps(dps_id="2", raw_value="closed", normalized_value=None)

    def test_nested_data_dps_open_closed(self):
        """Captured Tuya reports carry DPS under data.dps."""
        mapping = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            device_class="door",
            values={"open": "ON", "closed": "OFF"},
        )
        data = {
            "protocol": 4,
            "t": 2,
            "data": {"devId": "0123456789abcdefabcd", "dps": {"1": "closed"}},
        }
        result = extract_dps(data, {"1": mapping})

        assert result == [
            DecodedDps(dps_id="1", raw_value="closed", normalized_value="OFF")
        ]

    def test_unknown_dps_preserved(self):
        """DPS without a mapping should have normalized_value=None."""
        data = {"dps": {"101": "some_value"}}
        result = extract_dps(data, None)
        assert len(result) == 1
        assert result[0] == DecodedDps(dps_id="101", raw_value="some_value", normalized_value=None)

    def test_empty_dps_dict(self):
        result = extract_dps({"dps": {}}, None)
        assert result == []

    def test_no_dps_key(self):
        result = extract_dps({"other": "data"}, None)
        assert result == []

    def test_value_not_in_mapping(self):
        """If raw value has no entry in the mapping values, normalized stays None."""
        mapping = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            values={"open": "ON", "closed": "OFF"},
        )
        data = {"dps": {"1": "something_else"}}
        result = extract_dps(data, {"1": mapping})
        assert result[0].normalized_value is None


# ---------------------------------------------------------------------------
# decode_mqtt_payload (end-to-end)
# ---------------------------------------------------------------------------

class TestDecodeMqttPayload:
    def test_door_sensor_open(self):
        mapping = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            device_class="door",
            values={"open": "ON", "closed": "OFF"},
        )
        raw = _build_raw_payload({"dps": {"1": "open"}})
        event = decode_mqtt_payload(raw, TEST_KEY, {"1": mapping})
        assert event.protocol == 2
        assert len(event.dps_list) == 1
        assert event.dps_list[0] == DecodedDps(
            dps_id="1", raw_value="open", normalized_value="ON"
        )

    def test_door_sensor_closed(self):
        mapping = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            device_class="door",
            values={"open": "ON", "closed": "OFF"},
        )
        raw = _build_raw_payload({"dps": {"1": "closed"}})
        event = decode_mqtt_payload(raw, TEST_KEY, {"1": mapping})
        assert event.dps_list[0] == DecodedDps(
            dps_id="1", raw_value="closed", normalized_value="OFF"
        )

    def test_unknown_dps_passed_through(self):
        """Unknown DPS IDs (no mapping) should still appear with normalized_value=None."""
        raw = _build_raw_payload({"dps": {"1": "open", "101": "mystery"}})
        event = decode_mqtt_payload(raw, TEST_KEY)
        assert len(event.dps_list) == 2
        # DPS 1 has no mapping -> normalized is None
        assert event.dps_list[0].normalized_value is None
        assert event.dps_list[1] == DecodedDps(
            dps_id="101", raw_value="mystery", normalized_value=None
        )

    def test_mixed_known_and_unknown_dps(self):
        """Both mapped and unmapped DPS in a single payload."""
        mapping_1 = DeviceMapping(
            dps="1",
            platform="binary_sensor",
            values={"open": "ON", "closed": "OFF"},
        )
        mapping_2 = DeviceMapping(
            dps="2",
            platform="sensor",
            values={"high": "100", "low": "0"},
        )
        raw = _build_raw_payload({"dps": {"1": "open", "2": "high", "99": "unknown_val"}})
        event = decode_mqtt_payload(raw, TEST_KEY, {"1": mapping_1, "2": mapping_2})
        assert len(event.dps_list) == 3
        assert event.dps_list[0].normalized_value == "ON"
        assert event.dps_list[1].normalized_value == "100"
        assert event.dps_list[2].normalized_value is None

    def test_bad_envelope_propagates(self):
        with pytest.raises(ValueError, match="too short"):
            decode_mqtt_payload(b"short", TEST_KEY)

    def test_wrong_key_propagates(self):
        raw = _build_raw_payload({"dps": {"1": "open"}})
        with pytest.raises(ValueError, match="AES decryption failed"):
            decode_mqtt_payload(raw, _WRONG_KEY)
