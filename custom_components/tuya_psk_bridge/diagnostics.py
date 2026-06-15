"""Diagnostics support for the Tuya PSK Bridge integration."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SENSITIVE_FIELDS

_LOGGER = logging.getLogger(__name__)

REDACTED = "**REDACTED**"


def _redact_secrets(data: Any) -> Any:
    """Recursively redact sensitive fields from a data structure."""
    if isinstance(data, dict):
        return {k: REDACTED if k in SENSITIVE_FIELDS else _redact_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry with all secrets redacted."""
    redacted_data = _redact_secrets(deepcopy(entry.data))

    device_count = len(entry.data.get("devices", []))

    return {
        "domain": DOMAIN,
        "title": entry.title,
        "entry_id": entry.entry_id,
        "device_count": device_count,
        "data": redacted_data,
    }
