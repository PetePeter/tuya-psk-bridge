"""Tuya MQTT payload decoder — strip envelope, AES decrypt, JSON parse, DPS extract."""

from __future__ import annotations

import json
import logging
from typing import Any

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad

from .models import DecodedDps, DecodedEvent, DeviceMapping

logger = logging.getLogger(__name__)

# The Tuya 2.2 protocol envelope header occupies 15 bytes:
#   b"2.2"  (3 bytes protocol marker)
#   12 bytes of envelope metadata (sequence, timestamp, flags, reserved)
# Followed by the AES-128-ECB encrypted ciphertext.
_PROTOCOL_MARKER = b"2.2"
_ENVELOPE_HEADER_LEN = 15


def strip_envelope(raw: bytes) -> tuple[str, bytes]:
    """Strip the Tuya 2.2 protocol envelope and extract the ciphertext.

    The first 15 bytes are the header: 3-byte protocol marker (``2.2``) followed
    by 12 reserved zero bytes.  Everything after is AES ciphertext.

    Args:
        raw: The raw MQTT payload bytes.

    Returns:
        A tuple of (protocol_version_string, ciphertext).

    Raises:
        ValueError: If the payload is too short or does not start with the
            expected protocol marker.
    """
    if len(raw) <= _ENVELOPE_HEADER_LEN:
        raise ValueError(
            f"Payload too short ({len(raw)} bytes); "
            f"expected at least {_ENVELOPE_HEADER_LEN + 1}"
        )

    protocol = raw[:3].decode("ascii", errors="replace")
    if not raw.startswith(_PROTOCOL_MARKER):
        raise ValueError(
            f"Unsupported protocol marker '{protocol}'; expected '2.2'"
        )

    ciphertext = raw[_ENVELOPE_HEADER_LEN:]
    return protocol, ciphertext


def decrypt_payload(ciphertext: bytes, local_key: str) -> bytes:
    """Decrypt an AES-128-ECB ciphertext using the device local key.

    The local key string (expected to be exactly 16 characters) is encoded as
    UTF-8 and used directly as the AES key.

    Args:
        ciphertext: AES-128-ECB encrypted bytes (PKCS#7 padded).
        local_key: The device's local key string.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        ValueError: If the ciphertext cannot be decrypted or has invalid padding.
    """
    key_bytes = local_key.encode("utf-8")[:16]
    try:
        cipher = AES.new(key_bytes, AES.MODE_ECB)
        padded = cipher.decrypt(ciphertext)
        return unpad(padded, AES.block_size)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"AES decryption failed: {exc}") from exc


def parse_decoded_json(plaintext: bytes) -> dict[str, Any]:
    """Parse decrypted plaintext bytes as JSON.

    Args:
        plaintext: The decrypted JSON bytes.

    Returns:
        The parsed JSON dictionary.

    Raises:
        ValueError: If the bytes are not valid JSON.
    """
    try:
        data = json.loads(plaintext)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON in decrypted payload: {exc}") from exc


def extract_dps(
    data: dict[str, Any],
    device_mappings: dict[str, DeviceMapping] | None = None,
) -> list[DecodedDps]:
    """Extract and optionally normalize DPS entries from the decoded data dict.

    The Tuya payload typically has a top-level ``"dps"`` key whose value is a
    dict mapping DPS IDs to raw string values.  Each entry is converted to a
    :class:`DecodedDps`.  If ``device_mappings`` is provided and a mapping exists
    for a given DPS ID, the ``normalized_value`` is set from the mapping's
    ``values`` dict; otherwise ``normalized_value`` remains ``None``.

    Args:
        data: The parsed JSON dictionary from the Tuya payload.
        device_mappings: Optional mapping of DPS ID strings to DeviceMapping objects.

    Returns:
        A list of DecodedDps instances.  Returns an empty list if the data
        has no ``"dps"`` key or the dps dict is empty.
    """
    dps_raw: dict[str, Any] = data.get("dps", {})
    if not isinstance(dps_raw, dict) or not dps_raw:
        return []

    mappings = device_mappings or {}
    results: list[DecodedDps] = []

    for dps_id, raw_value in dps_raw.items():
        raw_str = str(raw_value)
        normalized: str | None = None

        mapping = mappings.get(dps_id)
        if mapping and raw_str in mapping.values:
            normalized = mapping.values[raw_str]

        results.append(DecodedDps(dps_id=dps_id, raw_value=raw_str, normalized_value=normalized))

    return results


def decode_mqtt_payload(
    raw: bytes,
    local_key: str,
    device_mappings: dict[str, DeviceMapping] | None = None,
) -> DecodedEvent:
    """Full decode pipeline: strip envelope, decrypt, parse JSON, extract DPS.

    Args:
        raw: The raw MQTT payload bytes from the Tuya device.
        local_key: The device's 16-character local key for AES decryption.
        device_mappings: Optional mapping of DPS IDs to DeviceMapping objects
            for value normalization.

    Returns:
        A DecodedEvent containing the protocol version and extracted DPS list.

    Raises:
        ValueError: If any step in the pipeline fails (bad envelope, decryption,
            JSON parse, etc.).
    """
    protocol, ciphertext = strip_envelope(raw)
    plaintext = decrypt_payload(ciphertext, local_key)
    data = parse_decoded_json(plaintext)
    dps_list = extract_dps(data, device_mappings)

    return DecodedEvent(
        device_id="",  # device_id is not in the payload itself; set by the caller
        protocol=int(protocol.split(".")[0]),
        dps_list=dps_list,
    )
