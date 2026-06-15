"""Tuya PSK Bridge custom integration for Home Assistant.

This integration stores device configurations and coordinates with the
tuya-psk-bridge add-on/container. The bridge process handles actual
Tuya device communication and publishes entity state via MQTT discovery.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya PSK Bridge from a config entry.

    MVP: stores device configs in config entry data. The bridge process
    runs separately as an add-on and publishes MQTT discovery messages.
    """
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "devices": entry.data.get("devices", []),
    }

    _LOGGER.info(
        "Tuya PSK Bridge setup complete with %d device(s)",
        len(entry.data.get("devices", [])),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Tuya PSK Bridge config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)

    # Clean up DOMAIN dict if last entry removed
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    _LOGGER.info("Tuya PSK Bridge unloaded")
    return True
