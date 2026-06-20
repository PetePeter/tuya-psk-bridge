"""Bridge runtime that wires the PSK server to the MQTT decoder and HA MQTT broker.

Creates a :class:`PskMqttServer` for incoming Tuya TLS-PSK connections and a
:class:`HaMqttPublisher` from :mod:`ha_mqtt` for publishing decoded events to
Home Assistant.  Discovery configurations are published as retained messages on
startup; state updates are also published retained on each device event so HA
restores the last-known value after a restart.

Lifecycle
---------
Call :meth:`BridgeRuntime.start` to begin listening and publishing.
Call :meth:`BridgeRuntime.stop` (or send ``SIGINT`` / ``SIGTERM``) for
graceful shutdown.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from .ha_mqtt import HaMqttPublisher, build_state_payload
from .models import BridgeConfig, DecodedDps, DecodedEvent, DeviceConfig
from .server import PskMqttServer

logger = logging.getLogger(__name__)


class BridgeRuntime:
    """Orchestrates the PSK server and HA MQTT publisher.

    Args:
        config: Fully loaded bridge configuration.
        psk_hint: The PSK hint bytes for TLS-PSK handshake.
    """

    def __init__(self, config: BridgeConfig, psk_hint: bytes) -> None:
        self._config = config
        self._hint = psk_hint
        self._publisher = HaMqttPublisher(
            host=config.ha_mqtt_host,
            port=config.ha_mqtt_port,
            username=config.ha_mqtt_username,
            password=config.ha_mqtt_password,
        )
        self._server: PskMqttServer | None = None

        # Device-id -> DeviceConfig for fast lookup.
        self._device_map: dict[str, DeviceConfig] = {
            dev.device_id: dev for dev in config.devices
        }

    # -- public API -----------------------------------------------------------

    def start(self) -> None:
        """Start the HA MQTT client, publish discovery, start the PSK server,
        and register signal handlers for graceful shutdown."""
        self._publisher.connect()

        # Wait briefly for MQTT connection before publishing discovery.
        _wait_for(lambda: self._publisher.connected, timeout=5.0)

        self._publisher.publish_all_discovery(self._config)
        for device in self._config.devices:
            self._publisher.publish_availability(device, True)

        self._server = PskMqttServer(
            config=self._config,
            psk_hint=self._hint,
            event_callback=self._on_device_event,
        )
        self._server.start()

        # Register signal handlers for graceful shutdown.
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("BridgeRuntime started")

    def stop(self) -> None:
        """Stop the PSK server and disconnect from HA MQTT."""
        if self._server:
            self._server.stop()
            self._server = None
        for device in self._config.devices:
            self._publisher.publish_availability(device, False)
        self._publisher.disconnect()
        logger.info("BridgeRuntime stopped")

    # -- internals -------------------------------------------------------------

    def _on_device_event(self, device_id: str, event: DecodedEvent) -> None:
        """Callback from PskMqttServer — publish each DPS as HA state."""
        device = self._device_map.get(device_id)
        if device is None:
            logger.warning("No device config for device_id=%s; cannot publish state", device_id)
            return

        for dps in event.dps_list:
            self._publisher.publish_state(device, dps)
            logger.debug(
                "Published state for %s DPS %s (raw=%s, norm=%s)",
                device_id,
                dps.dps_id,
                dps.raw_value,
                dps.normalized_value,
            )

    def _signal_handler(self, _signum: int, _frame: Any) -> None:
        """Handle SIGINT/SIGTERM by stopping the runtime."""
        logger.info("Received signal %d; shutting down", _signum)
        self.stop()


def _wait_for(predicate: Any, timeout: float = 5.0, poll_interval: float = 0.1) -> bool:
    """Block until *predicate* returns a truthy value or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_interval)
    return False
