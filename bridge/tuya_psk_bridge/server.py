"""TLS-PSK MQTT server that terminates device connections and dispatches decoded events.

Creates a listening socket, performs the TLS-PSK handshake using :func:`derive_psk`,
parses MQTT packets from the decrypted stream, and calls a registered callback
with :class:`DecodedEvent` objects on every device publish.

 graceful-shutdown support
----------------------------
Call :meth:`PskMqttServer.stop` or send ``SIGINT`` / ``SIGTERM`` to trigger
orderly teardown.

 sslpsk availability
--------------------
The ``sslpsk`` package provides the TLS-PSK socket layer.  If it is not
installed the constructor raises :exc:`ImportError` with a clear message.
Install it with ``pip install sslpsk`` (or add it to your dependencies).
"""

from __future__ import annotations

import logging
import select
import signal
import socket
import ssl
import struct
import threading
from typing import Any, Callable

from .models import BridgeConfig, DecodedEvent, DeviceConfig
from .psk_frontend import derive_psk

logger = logging.getLogger(__name__)

# Callback signature: (device_id: str, event: DecodedEvent) -> None
EventCallback = Callable[[str, DecodedEvent], None]

# ---------------------------------------------------------------------------
# sslpsk import — graceful degradation
# ---------------------------------------------------------------------------

try:
    import sslpsk  # noqa: F401 — re-exported availability check

    _HAS_SSLPSK = True
except ImportError:
    sslpsk = None  # type: ignore[assignment]
    _HAS_SSLPSK = False

# ---------------------------------------------------------------------------
# MQTT constants
# ---------------------------------------------------------------------------

# MQTT Control Packet types (high nibble of first byte)
_MQTT_CONNECT = 0x10
_MQTT_CONNACK = 0x20
_MQTT_PUBLISH = 0x30
# Packet routing masks the fixed header down to the control packet type nibble.
# SUBSCRIBE packets have required flags 0x02 on the wire, so 0x82 becomes 0x80
# after masking.
_MQTT_SUBSCRIBE = 0x80
_MQTT_SUBACK = 0x90
_MQTT_PINGREQ = 0xC0
_MQTT_PINGRESP = 0xD0
_MQTT_DISCONNECT = 0xE0

_TUYA_OUT_TOPIC_PREFIX = b"smart/device/out/"


def _has_sslpsk() -> None:
    """Raise ImportError with guidance if sslpsk is not installed."""
    if not _HAS_SSLPSK:
        raise ImportError(
            "The 'sslpsk' package is required for TLS-PSK termination but is "
            "not installed. Install it with: pip install sslpsk. "
            "If sslpsk has no wheels for your platform, you may need to build "
            "from source (requires OpenSSL development headers)."
        )


# ---------------------------------------------------------------------------
# Lightweight MQTT packet parser
# ---------------------------------------------------------------------------


def _decode_remaining_length(data: bytes, offset: int) -> tuple[int, int]:
    """Decode the MQTT variable-length remaining-length field.

    Returns (remaining_length, bytes_consumed).
    """
    multiplier = 1
    value = 0
    idx = offset
    while True:
        if idx >= len(data):
            raise ValueError("Incomplete MQTT remaining-length field")
        encoded_byte = data[idx]
        value += (encoded_byte & 0x7F) * multiplier
        idx += 1
        multiplier *= 128
        if not (encoded_byte & 0x80):
            break
        if multiplier > 128 * 128 * 128:
            raise ValueError("MQTT remaining-length field too large")
    return value, idx - offset


def _parse_mqtt_connect(data: bytes) -> str | None:
    """Extract the MQTT client ID from a CONNECT packet.

    Returns the client ID string, or None if parsing fails.
    """
    try:
        _, header_len = _decode_remaining_length(data, 1)
        offset = 1 + header_len
        # Protocol name (2-byte length + string)
        proto_len = struct.unpack_from("!H", data, offset)[0]
        offset += 2 + proto_len  # skip protocol name
        offset += 1  # protocol level
        offset += 1  # connect flags
        offset += 2  # keep alive
        # Client ID (2-byte length + string)
        client_id_len = struct.unpack_from("!H", data, offset)[0]
        offset += 2
        if offset + client_id_len > len(data):
            return None
        return data[offset : offset + client_id_len].decode("utf-8", errors="replace")
    except (struct.error, ValueError, IndexError):
        return None


def _parse_mqtt_subscribe(data: bytes) -> tuple[int | None, str | None]:
    """Extract the first topic filter from a SUBSCRIBE packet.

    Returns (packet_id, topic_filter), or (None, None) if parsing fails.
    """
    try:
        _, header_len = _decode_remaining_length(data, 1)
        offset = 1 + header_len
        # Packet identifier (2 bytes)
        packet_id = struct.unpack_from("!H", data, offset)[0]
        offset += 2
        # Topic filter (2-byte length + string)
        topic_len = struct.unpack_from("!H", data, offset)[0]
        offset += 2
        if offset + topic_len > len(data):
            return None, None
        topic = data[offset : offset + topic_len].decode("utf-8", errors="replace")
        return packet_id, topic
    except (struct.error, ValueError, IndexError):
        return None, None


def _parse_mqtt_publish(data: bytes) -> tuple[str | None, bytes | None]:
    """Extract the topic and payload from a PUBLISH packet.

    Returns (topic_string_or_None, payload_or_None).
    """
    try:
        dup_qos_retain = data[0] & 0x0F
        qos = (dup_qos_retain >> 1) & 0x03

        remaining_len, header_len = _decode_remaining_length(data, 1)
        payload_start = 1 + header_len

        # Topic (2-byte length + string)
        topic_len = struct.unpack_from("!H", data, payload_start)[0]
        topic_offset = payload_start + 2
        if topic_offset + topic_len > len(data):
            return None, None
        topic = data[topic_offset : topic_offset + topic_len].decode("utf-8", errors="replace")

        # Packet identifier only present for QoS > 0
        content_offset = topic_offset + topic_len
        if qos > 0:
            content_offset += 2

        payload = data[content_offset : payload_start + remaining_len]
        return topic, payload
    except (struct.error, ValueError, IndexError):
        return None, None


def _extract_device_id_from_topic(topic: str) -> str | None:
    """Extract the device ID from a Tuya outgoing topic.

    Expected format: ``smart/device/out/<device_id>``.
    """
    prefix = _TUYA_OUT_TOPIC_PREFIX.decode("ascii")
    if topic.startswith(prefix):
        device_id = topic[len(prefix) :]
        # Topic may have a sub-topic suffix separated by '/'
        parts = device_id.split("/", 1)
        return parts[0] if parts[0] else None
    return None


def _extract_device_id_from_client_id(client_id: str) -> str | None:
    """Try to extract a Tuya device ID from the MQTT CONNECT client ID.

    Tuya devices typically use the device ID as the MQTT client ID, but the
    format is not guaranteed.  We return the raw client ID and let the caller
    decide whether it is a valid device identifier.
    """
    if client_id and len(client_id) >= 16:
        return client_id.strip()
    return None


def _packet_type_name(packet_type: int) -> str:
    """Return a safe display name for an MQTT control packet type."""
    names = {
        _MQTT_CONNECT: "CONNECT",
        _MQTT_CONNACK: "CONNACK",
        _MQTT_PUBLISH: "PUBLISH",
        _MQTT_SUBSCRIBE: "SUBSCRIBE",
        _MQTT_SUBACK: "SUBACK",
        _MQTT_PINGREQ: "PINGREQ",
        _MQTT_PINGRESP: "PINGRESP",
        _MQTT_DISCONNECT: "DISCONNECT",
    }
    return names.get(packet_type, f"UNKNOWN_0x{packet_type:02X}")


# ---------------------------------------------------------------------------
# PskMqttServer
# ---------------------------------------------------------------------------


class _DeviceSession:
    """Tracks state for a single connected Tuya device.

    Not thread-safe — accessed only from the server's select loop thread.
    """

    __slots__ = ("sock", "device_id", "buffer", "client_id")

    def __init__(self, sock: ssl.SSLSocket, remote_addr: tuple[str, int]) -> None:
        self.sock = sock
        self.device_id: str | None = None
        self.buffer = bytearray()
        self.client_id: str | None = None
        logger.info("New TLS-PSK connection from %s:%s", remote_addr[0], remote_addr[1])


class PskMqttServer:
    """Listens for Tuya device TLS-PSK connections and dispatches decoded MQTT events.

    Usage::

        config = load_config("bridge.yaml")
        server = PskMqttServer(
            config=config,
            psk_hint=psk_hint_bytes,
            event_callback=my_handler,
        )
        server.start()
        # ... later ...
        server.stop()

    Args:
        config: Bridge configuration (listen_host, mqtt_psk_port, devices).
        psk_hint: The PSK hint bytes to present during the TLS-PSK handshake.
        event_callback: Called with ``(device_id: str, event: DecodedEvent)``
            whenever a decoded publish is received from a device.
    """

    def __init__(
        self,
        config: BridgeConfig,
        psk_hint: bytes,
        event_callback: EventCallback,
    ) -> None:
        _has_sslpsk()

        self._config = config
        self._hint = psk_hint
        self._event_callback = event_callback
        self._sessions: list[_DeviceSession] = []
        self._server_sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Build a device-id -> DeviceConfig lookup for fast matching.
        self._device_map: dict[str, DeviceConfig] = {
            dev.device_id: dev for dev in config.devices
        }

    # -- public API ----------------------------------------------------------

    def start(self) -> None:
        """Bind, listen, and spawn the select-loop thread.

        Raises:
            OSError: If the listen address/port cannot be bound (e.g. another
                process already using the port, or host networking misconfiguration).
        """
        with self._lock:
            if self._running:
                logger.warning("PskMqttServer already running")
                return

            self._server_sock = self._bind()
            self._running = True
            self._thread = threading.Thread(target=self._select_loop, daemon=True, name="psk-server")
            self._thread.start()
            logger.info(
                "PskMqttServer listening on %s:%d",
                self._config.listen_host,
                self._config.mqtt_psk_port,
            )

    def stop(self) -> None:
        """Signal the select loop to exit and wait for it to finish."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        self._close_all_sessions()
        logger.info("PskMqttServer stopped")

    @property
    def running(self) -> bool:
        """Whether the server select loop is active."""
        return self._running

    # -- internals -----------------------------------------------------------

    def _bind(self) -> socket.socket:
        """Create and bind the TCP listening socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self._config.listen_host, self._config.mqtt_psk_port))
        except OSError as exc:
            sock.close()
            raise OSError(
                f"Cannot bind to {self._config.listen_host}:{self._config.mqtt_psk_port}: "
                f"{exc}. Check that the port is free and the host address is correct "
                f"(for Docker/host networking, use 0.0.0.0 not 127.0.0.1)."
            ) from exc
        sock.listen(16)
        sock.setblocking(False)
        return sock

    def _wrap_psk(self, client_sock: socket.socket) -> ssl.SSLSocket:
        """Perform the TLS-PSK handshake on an accepted TCP connection."""
        return sslpsk.wrap_socket(  # type: ignore[attr-defined]
            client_sock,
            server_side=True,
            ssl_version=ssl.PROTOCOL_TLSv1_2,
            ciphers="PSK-AES128-CBC-SHA256",
            psk=lambda identity: derive_psk(identity, self._hint),
            hint=self._hint,
        )

    def _on_device_event(self, device_id: str, raw_payload: bytes) -> None:
        """Look up the device, decode the payload, and fire the callback."""
        device = self._device_map.get(device_id)
        if device is None:
            logger.warning("Received publish for unknown device_id=%s; skipping decode", device_id)
            return

        from .mqtt_decoder import decode_mqtt_payload

        # Build mappings dict from device config.
        mappings = {m.dps: m for m in device.mappings}

        try:
            event = decode_mqtt_payload(raw_payload, device.local_key, mappings)
            # Stamp the device_id — decoder leaves it empty.
            enriched = DecodedEvent(
                device_id=device_id,
                protocol=event.protocol,
                dps_list=event.dps_list,
                timestamp=event.timestamp,
            )
            self._event_callback(device_id, enriched)
        except ValueError as exc:
            logger.warning("Failed to decode payload from device %s: %s", device_id, exc)

    def _handle_connect(self, session: _DeviceSession) -> None:
        """Process a CONNECT packet: extract client ID, send CONNACK."""
        client_id = _parse_mqtt_connect(bytes(session.buffer))
        if client_id:
            session.client_id = client_id
            guessed_id = _extract_device_id_from_client_id(client_id)
            if guessed_id:
                session.device_id = guessed_id
                logger.info("CONNECT client_id=%s (guessed device_id=%s)", client_id, guessed_id)

        # Send CONNACK (session_present=0, return_code=0)
        try:
            session.sock.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
            logger.debug("Sent CONNACK to device_id=%s", session.device_id or "<unknown>")
        except OSError as exc:
            logger.warning("Failed to send CONNACK: %s", exc)

    def _handle_subscribe(self, session: _DeviceSession) -> None:
        """Process a SUBSCRIBE packet: extract topic, update device_id, send SUBACK."""
        packet_id, topic = _parse_mqtt_subscribe(bytes(session.buffer))
        if topic:
            device_id = _extract_device_id_from_topic(topic)
            if device_id and session.device_id != device_id:
                session.device_id = device_id
                logger.info("SUBSCRIBE topic=%s (device_id=%s)", topic, device_id)

        # Send SUBACK with QoS 0 granted for the first topic filter
        try:
            # Minimal SUBACK: header(0x90), remaining_len, packet_id(x2), return_code(0)
            response_packet_id = packet_id if packet_id is not None else 1
            session.sock.sendall(
                bytes(
                    [
                        0x90,
                        0x03,
                        (response_packet_id >> 8) & 0xFF,
                        response_packet_id & 0xFF,
                        0x00,
                    ]
                )
            )
            logger.debug(
                "Sent SUBACK packet_id=%d to device_id=%s",
                response_packet_id,
                session.device_id or "<unknown>",
            )
        except OSError as exc:
            logger.warning("Failed to send SUBACK: %s", exc)

    def _handle_publish(self, session: _DeviceSession) -> None:
        """Process a PUBLISH packet: extract topic/payload, decode, dispatch."""
        topic, payload = _parse_mqtt_publish(bytes(session.buffer))
        if not topic or not payload:
            return

        device_id = _extract_device_id_from_topic(topic)
        if device_id:
            session.device_id = device_id
            self._on_device_event(device_id, payload)
        else:
            logger.debug("PUBLISH to non-Tuya topic=%s; ignoring", topic)

    def _process_buffer(self, session: _DeviceSession) -> None:
        """Try to parse a complete MQTT packet from the session buffer.

        Consumes bytes from the buffer when a complete packet is found.
        Battery-powered devices may send a burst then disconnect — we process
        whatever we can before the socket closes.
        """
        if not session.buffer:
            return

        packet_type = session.buffer[0] & 0xF0
        try:
            remaining_len, header_len = _decode_remaining_length(bytes(session.buffer), 1)
        except ValueError:
            # Incomplete remaining-length — wait for more data.
            return

        total_len = 1 + header_len + remaining_len
        if len(session.buffer) < total_len:
            # Not enough data yet for the full packet.
            return

        # We have a complete packet.  Process it.
        logger.debug(
            "MQTT packet received type=%s remaining_len=%d total_len=%d buffered=%d",
            _packet_type_name(packet_type),
            remaining_len,
            total_len,
            len(session.buffer),
        )
        if packet_type == _MQTT_CONNECT:
            self._handle_connect(session)
        elif packet_type == _MQTT_SUBSCRIBE:
            self._handle_subscribe(session)
        elif packet_type == _MQTT_PUBLISH:
            self._handle_publish(session)
        elif packet_type == _MQTT_PINGREQ:
            try:
                session.sock.sendall(bytes([_MQTT_PINGRESP, 0x00]))
            except OSError:
                pass
        elif packet_type == _MQTT_DISCONNECT:
            logger.debug("Client sent DISCONNECT")
        else:
            logger.debug("Unhandled MQTT packet type 0x%02X", packet_type)

        # Consume the packet.
        del session.buffer[:total_len]

    def _select_loop(self) -> None:
        """Main event loop: accept connections, read data, parse MQTT."""
        assert self._server_sock is not None

        while self._running:
            try:
                readables = [self._server_sock] + [s.sock for s in self._sessions]
                ready, _, _ = select.select(readables, [], [], 0.5)
            except (OSError, ValueError):
                # Socket closed during select — stop.
                break

            for ready_sock in ready:
                if ready_sock is self._server_sock:
                    self._accept_connection()
                    continue

                self._read_from_session(ready_sock)

            # Garbage-collect closed sessions.
            self._prune_dead_sessions()

    def _accept_connection(self) -> None:
        """Accept a new TCP connection and wrap it with TLS-PSK."""
        assert self._server_sock is not None
        try:
            client, addr = self._server_sock.accept()
        except OSError:
            return

        try:
            ssl_sock = self._wrap_psk(client)
            ssl_sock.setblocking(False)
            self._sessions.append(_DeviceSession(ssl_sock, addr))
        except ssl.SSLError as exc:
            logger.warning("TLS-PSK handshake failed from %s:%s: %s", addr[0], addr[1], exc)
            try:
                client.close()
            except OSError:
                pass
        except Exception as exc:
            logger.warning("Failed to accept connection from %s:%s: %s", addr[0], addr[1], exc)
            try:
                client.close()
            except OSError:
                pass

    def _read_from_session(self, sock: ssl.SSLSocket) -> None:
        """Read data from a connected session and attempt packet parsing."""
        session = self._find_session(sock)
        if session is None:
            return

        try:
            data = sock.recv(4096)
            if data:
                logger.debug(
                    "Read %d byte(s) from device_id=%s",
                    len(data),
                    session.device_id or "<unknown>",
                )
                session.buffer.extend(data)
                # Process all complete packets in the buffer.
                while session.buffer:
                    buf_snapshot = len(session.buffer)
                    self._process_buffer(session)
                    if len(session.buffer) == buf_snapshot:
                        # No progress — either incomplete or handled.
                        # Try once more in case we just consumed the last bytes.
                        self._process_buffer(session)
                        break
            else:
                # Connection closed (battery device done or network drop).
                logger.debug(
                    "TLS session returned EOF for device_id=%s",
                    session.device_id or "<unknown>",
                )
                self._close_session(session)
        except ssl.SSLEOFError:
            # Battery devices often disconnect without a proper TLS close_notify.
            logger.debug("TLS session closed by device (battery disconnect)")
            self._close_session(session)
        except (ssl.SSLError, OSError) as exc:
            logger.debug("Socket error on session: %s", exc)
            self._close_session(session)

    def _find_session(self, sock: ssl.SSLSocket) -> _DeviceSession | None:
        for session in self._sessions:
            if session.sock is sock:
                return session
        return None

    def _close_session(self, session: _DeviceSession) -> None:
        """Close a single device session and remove it from tracking."""
        try:
            self._sessions.remove(session)
        except ValueError:
            pass
        try:
            session.sock.close()
        except OSError:
            pass
        if session.device_id:
            logger.info("Device %s disconnected", session.device_id)

    def _close_all_sessions(self) -> None:
        """Close every tracked session."""
        for session in list(self._sessions):
            try:
                session.sock.close()
            except OSError:
                pass
        self._sessions.clear()

    def _prune_dead_sessions(self) -> None:
        """Remove sessions whose sockets are no longer viable."""
        dead = []
        for session in self._sessions:
            try:
                # Peek — if the socket reports an error, it is dead.
                session.sock.getpeername()
            except OSError:
                dead.append(session)
        for session in dead:
            self._close_session(session)
