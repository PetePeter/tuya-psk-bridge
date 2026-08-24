"""Outbound command encoding — bridge to Tuya device.

The inverse of :mod:`mqtt_decoder`: build a Tuya 2.2 envelope around an
AES-128-ECB encrypted JSON command so a device accepts it on its inbound
topic.  Devices report on ``smart/device/out/<device_id>`` and listen on
``smart/device/in/<device_id>``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad

# Matches the 15-byte header the decoder strips: b"2.2" plus 12 metadata bytes.
_PROTOCOL_MARKER = b"2.2"
_ENVELOPE_PADDING = b"\x00" * 12

# Tuya uses protocol 5 for control messages sent to a device.
_CONTROL_PROTOCOL = 5

_TUYA_IN_TOPIC_PREFIX = "smart/device/in/"


def command_topic(device_id: str) -> str:
    """Return the Tuya inbound topic a device listens on for commands."""
    return f"{_TUYA_IN_TOPIC_PREFIX}{device_id}"


def encode_command(device_id: str, dps: dict[str, Any], local_key: str) -> bytes:
    """Encode a DPS command as an encrypted Tuya 2.2 payload.

    Args:
        device_id: The target device's Tuya identifier.
        dps: DPS id to value, e.g. ``{"1": True}``.
        local_key: The device's local key; the first 16 bytes are the AES key.

    Returns:
        The bytes to publish as the MQTT payload.
    """
    body = json.dumps(
        {
            "protocol": _CONTROL_PROTOCOL,
            "t": int(time.time()),
            "data": {"devId": device_id, "dps": dps},
        },
        separators=(",", ":"),
    ).encode("utf-8")

    cipher = AES.new(local_key.encode("utf-8")[:16], AES.MODE_ECB)
    ciphertext = cipher.encrypt(pad(body, AES.block_size))

    return _PROTOCOL_MARKER + _ENVELOPE_PADDING + ciphertext
