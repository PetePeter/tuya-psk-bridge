"""Tests for diagnostics redaction.

HA dependencies are mocked via conftest.py so tests run without
the homeassistant package installed.
"""

from __future__ import annotations

from custom_components.tuya_psk_bridge.const import SENSITIVE_FIELDS
from custom_components.tuya_psk_bridge.diagnostics import (
    REDACTED,
    _redact_secrets,
)


def _sample_devices() -> list[dict]:
    """Return test device data with secrets."""
    return [
        {
            "device_id": "abcdef12345678901234",
            "local_key": "super_secret_key_123",
            "name": "Front Door",
            "profile": "door_sensor",
            "mappings": [
                {
                    "dps": "1",
                    "platform": "binary_sensor",
                    "device_class": "door",
                    "values": {"open": "ON", "closed": "OFF"},
                }
            ],
        }
    ]


class TestRedactSecrets:
    """Tests for the _redact_secrets helper."""

    def test_redacts_local_key_in_dict(self) -> None:
        """Local key inside a dict must be replaced with REDACTED."""
        data = {"device_id": "aaaa", "local_key": "secret123"}
        result = _redact_secrets(data)

        assert result["local_key"] == REDACTED
        assert result["device_id"] == "aaaa"

    def test_redacts_nested_sensitive_fields(self) -> None:
        """Sensitive fields nested inside devices list must be redacted."""
        data = {"devices": _sample_devices()}
        result = _redact_secrets(data)

        device = result["devices"][0]
        assert device["local_key"] == REDACTED
        assert device["device_id"] == "abcdef12345678901234"

    def test_redacts_all_sensitive_fields(self) -> None:
        """Every field named in SENSITIVE_FIELDS is redacted."""
        for field in SENSITIVE_FIELDS:
            data = {field: "should_not_appear", "other": "keep"}
            result = _redact_secrets(data)
            assert result[field] == REDACTED
            assert result["other"] == "keep"

    def test_preserves_non_sensitive_fields(self) -> None:
        """Fields not in SENSITIVE_FIELDS are left untouched."""
        data = {
            "name": "Front Door",
            "profile": "door_sensor",
            "mappings": [{"dps": "1", "platform": "binary_sensor"}],
        }
        result = _redact_secrets(data)

        assert result["name"] == "Front Door"
        assert result["mappings"][0]["dps"] == "1"

    def test_no_secret_string_in_output(self) -> None:
        """Raw secret values must not appear anywhere in the output."""
        data = {"devices": _sample_devices()}
        result = _redact_secrets(data)

        output = str(result)
        assert "super_secret_key_123" not in output

    def test_redacts_multiple_devices(self) -> None:
        """All devices in a list must have their keys redacted."""
        devices = [
            {"device_id": "aaaaaaaaaaaaaaaaaaaa", "local_key": "key_one", "name": "Door A"},
            {"device_id": "bbbbbbbbbbbbbbbbbbbb", "local_key": "key_two", "name": "Door B"},
        ]
        result = _redact_secrets(devices)

        for device in result:
            assert device["local_key"] == REDACTED

    def test_handles_empty_data(self) -> None:
        """Empty structures pass through without error."""
        assert _redact_secrets({}) == {}
        assert _redact_secrets([]) == []
        assert _redact_secrets(None) is None

    def test_handles_list_of_dicts(self) -> None:
        """Lists containing dicts must be processed recursively."""
        data = [
            {"local_key": "alpha", "name": "A"},
            {"local_key": "beta", "name": "B"},
        ]
        result = _redact_secrets(data)

        assert result[0]["local_key"] == REDACTED
        assert result[1]["local_key"] == REDACTED
        assert result[0]["name"] == "A"

    def test_primitives_pass_through(self) -> None:
        """Strings, numbers, bools are returned as-is."""
        assert _redact_secrets("hello") == "hello"
        assert _redact_secrets(42) == 42
        assert _redact_secrets(True) is True
