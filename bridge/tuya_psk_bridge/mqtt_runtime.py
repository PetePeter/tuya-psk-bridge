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

from .ha_mqtt import HaMqttPublisher, build_state_payload, ha_command_topic
from .models import BridgeConfig, DecodedDps, DecodedEvent, DeviceConfig, DeviceMapping
from .psk_frontend import derive_psk, get_psk_deriver
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
        self._servers: list[PskMqttServer] = []

        # Device-id -> DeviceConfig for fast lookup.
        self._device_map: dict[str, DeviceConfig] = {
            dev.device_id: dev for dev in config.devices
        }

    # -- public API -----------------------------------------------------------

    @property
    def servers(self) -> list[PskMqttServer]:
        """The PSK listeners this runtime owns, one per configured port."""
        return list(self._servers)

    def build_servers(self) -> list[PskMqttServer]:
        """Create one PSK server per configured listen port, without starting.

        Devices do not fall back between endpoints, so each port needs its own
        listener sharing the same device configuration.
        """
        self._servers = [
            PskMqttServer(
                config=self._config,
                psk_hint=self._hint,
                event_callback=self._on_device_event,
                port=port,
                psk_deriver=self._deriver_for(port),
            )
            for port in self._config.listen_ports
        ]
        return self._servers

    def _deriver_for(self, port: int):
        """Pick the PSK derivation for a listener.

        The primary port keeps the proven derivation unconditionally — a lab
        variant is only ever applied to the additional ports.
        """
        if port == self._config.mqtt_psk_port or not self._config.psk_variant:
            return derive_psk
        logger.warning(
            "Port %d is using the experimental PSK variant '%s'",
            port,
            self._config.psk_variant,
        )
        return get_psk_deriver(self._config.psk_variant)

    def start_servers(self) -> None:
        """Bind and start every PSK listener."""
        for server in self.build_servers():
            server.start()

    def stop_servers(self) -> None:
        """Stop every PSK listener."""
        for server in self._servers:
            server.stop()

    def start(self) -> None:
        """Start the HA MQTT client, publish discovery, start the PSK servers,
        and register signal handlers for graceful shutdown."""
        self._publisher.connect()

        # Wait briefly for MQTT connection before publishing discovery.
        _wait_for(lambda: self._publisher.connected, timeout=5.0)

        self._publisher.publish_all_discovery(self._config)
        for device in self._config.devices:
            self._publisher.publish_availability(device, True)
        self._publisher.subscribe_commands(self._config, self.handle_ha_command)

        self.start_servers()

        # Register signal handlers for graceful shutdown.
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("BridgeRuntime started")

    def stop(self) -> None:
        """Stop the PSK servers and disconnect from HA MQTT."""
        self.stop_servers()
        self._servers = []
        for device in self._config.devices:
            self._publisher.publish_availability(device, False)
        self._publisher.disconnect()
        logger.info("BridgeRuntime stopped")

    def send_command(self, device_id: str, dps: dict[str, Any]) -> bool:
        """Route a command to whichever listener holds the device's session."""
        for server in self._servers:
            if server.send_command(device_id, dps):
                return True
        logger.warning("Command for %s was not delivered on any port", device_id)
        return False

    def handle_ha_command(self, topic: str, payload: str) -> None:
        """Translate an HA command topic message into a Tuya DPS command.

        Args:
            topic: The HA command topic, ``.../tuya_psk_<device_id>_<dps>/set``.
            payload: The HA state string, e.g. ``ON``.
        """
        resolved = self._resolve_command_topic(topic)
        if resolved is None:
            logger.warning("Ignoring command on unrecognized topic %s", topic)
            return

        device, mapping = resolved
        raw_value = _raw_value_for_state(mapping, payload)
        if raw_value is None:
            logger.warning(
                "No raw value maps to '%s' for device %s dps %s",
                payload,
                device.device_id,
                mapping.dps,
            )
            return

        self.send_command(device.device_id, {mapping.dps: raw_value})

    def _resolve_command_topic(
        self, topic: str
    ) -> tuple[DeviceConfig, DeviceMapping] | None:
        """Find the device and mapping a command topic addresses."""
        for device in self._config.devices:
            for mapping in device.mappings:
                if ha_command_topic(device, mapping) == topic:
                    return device, mapping
        return None

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


def _raw_value_for_state(mapping: DeviceMapping, state: str) -> Any | None:
    """Reverse a mapping: turn an HA state string back into a Tuya DPS value.

    Mappings are written raw-to-normalized (``{"True": "ON"}``), so commands
    have to walk them backwards. The recovered key is a string because YAML
    keys are strings; it is coerced back to the type the device sent.
    """
    for raw, normalized in mapping.values.items():
        if normalized == state:
            return _coerce_raw_value(raw)
    return None


def _coerce_raw_value(raw: str) -> Any:
    """Restore the Python type a DPS value had before ``str()`` flattened it."""
    if raw == "True":
        return True
    if raw == "False":
        return False
    try:
        return int(raw)
    except ValueError:
        return raw


def _wait_for(predicate: Any, timeout: float = 5.0, poll_interval: float = 0.1) -> bool:
    """Block until *predicate* returns a truthy value or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_interval)
    return False
