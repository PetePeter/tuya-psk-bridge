"""YAML configuration loader for the Tuya PSK bridge."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .models import BridgeConfig, DeviceConfig, DeviceMapping

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when the configuration file is missing required fields or invalid."""


def _resolve_secret(value: str) -> str:
    """Resolve a ``!secret`` placeholder from an environment variable.

    Checks ``TUYA_<NAME>`` and ``<NAME>`` (uppercased) environment variables.
    If neither is set, returns the raw placeholder string and logs a warning.

    Args:
        value: The placeholder string (e.g. "my_local_key").

    Returns:
        The resolved value, or the original placeholder if not found.
    """
    env_name_upper = value.upper()
    candidates = [f"TUYA_{env_name_upper}", env_name_upper]
    for candidate in candidates:
        env_val = os.environ.get(candidate)
        if env_val is not None:
            return env_val

    logger.warning(
        "Could not resolve !secret %s from env vars %s; leaving as-is",
        value,
        candidates,
    )
    return value


_SECRET_PATTERN = re.compile(r"^!secret\s+(.+)$")


def _deep_resolve_secrets(data: Any) -> Any:
    """Recursively walk a parsed YAML structure and resolve any ``!secret`` strings.

    Args:
        data: Arbitrary YAML-parsed data (dict, list, str, etc.).

    Returns:
        The same structure with secret placeholders resolved.
    """
    if isinstance(data, dict):
        return {k: _deep_resolve_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_deep_resolve_secrets(item) for item in data]
    if isinstance(data, str):
        match = _SECRET_PATTERN.match(data.strip())
        if match:
            return _resolve_secret(match.group(1).strip())
    return data


def _parse_mappings(raw_mappings: list[dict[str, Any]] | None) -> list[DeviceMapping]:
    """Convert raw mapping dicts into DeviceMapping objects.

    Args:
        raw_mappings: List of dicts with keys dps, platform, device_class, values.

    Returns:
        Parsed DeviceMapping list.

    Raises:
        ConfigError: If a mapping is missing the required "dps" or "platform" key.
    """
    if not raw_mappings:
        return []

    mappings: list[DeviceMapping] = []
    for entry in raw_mappings:
        if "dps" not in entry:
            raise ConfigError(f"Mapping missing required field 'dps': {entry}")
        if "platform" not in entry:
            raise ConfigError(f"Mapping missing required field 'platform': {entry}")
        mappings.append(
            DeviceMapping(
                dps=str(entry["dps"]),
                platform=str(entry["platform"]),
                device_class=entry.get("device_class"),
                values=entry.get("values", {}),
            )
        )
    return mappings


def _parse_devices(raw_devices: list[dict[str, Any]] | None) -> list[DeviceConfig]:
    """Convert raw device dicts into DeviceConfig objects.

    Args:
        raw_devices: List of dicts with device_id, local_key, name, profile, mappings.

    Returns:
        Parsed DeviceConfig list.

    Raises:
        ConfigError: If a device is missing required fields.
    """
    if not raw_devices:
        return []

    required_fields = ("device_id", "local_key", "name", "profile")
    devices: list[DeviceConfig] = []
    for entry in raw_devices:
        missing = [f for f in required_fields if f not in entry]
        if missing:
            raise ConfigError(
                f"Device entry missing required fields {missing}: {entry}"
            )
        devices.append(
            DeviceConfig(
                device_id=str(entry["device_id"]),
                local_key=str(entry["local_key"]),
                name=str(entry["name"]),
                profile=str(entry["profile"]),
                mappings=_parse_mappings(entry.get("mappings")),
            )
        )
    return devices


def load_config(path: str | Path) -> BridgeConfig:
    """Load and validate a YAML bridge configuration file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A fully populated BridgeConfig instance.

    Raises:
        ConfigError: If the file cannot be read, parsed, or is missing required fields.
        FileNotFoundError: If the config file does not exist.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}")

    # Resolve any !secret placeholders that came through as strings
    data = _deep_resolve_secrets(raw)

    # Validate required top-level fields
    required_top = ("listen_host", "mqtt_psk_port", "ha_mqtt_host", "ha_mqtt_port", "log_level")
    missing_top = [f for f in required_top if f not in data]
    if missing_top:
        raise ConfigError(f"Config missing required fields: {missing_top}")

    return BridgeConfig(
        listen_host=str(data["listen_host"]),
        mqtt_psk_port=int(data["mqtt_psk_port"]),
        ha_mqtt_host=str(data["ha_mqtt_host"]),
        ha_mqtt_port=int(data["ha_mqtt_port"]),
        ha_mqtt_username=data.get("ha_mqtt_username"),
        ha_mqtt_password=data.get("ha_mqtt_password"),
        log_level=str(data["log_level"]),
        devices=_parse_devices(data.get("devices")),
    )
