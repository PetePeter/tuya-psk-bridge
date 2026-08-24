"""Tests for serving more than one TLS-PSK listener port.

Door sensors arrive on 8886 and the GIEX water timer on 8883, so the bridge
must bind both.  The single-port ``mqtt_psk_port`` key stays valid because it
is what the deployed add-on options already use.
"""

from __future__ import annotations

import socket

import pytest

from tuya_psk_bridge.config import ConfigError, load_config
from tuya_psk_bridge.models import BridgeConfig
from tuya_psk_bridge.server import PskMqttServer


def _write_config(tmp_path, body: str):
    path = tmp_path / "bridge.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_BASE = (
    'listen_host: "127.0.0.1"\n'
    'ha_mqtt_host: "127.0.0.1"\n'
    "ha_mqtt_port: 1883\n"
    'log_level: "INFO"\n'
)


def _free_port() -> int:
    """Ask the OS for a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TestListenPortConfig:
    """``mqtt_psk_ports`` is the multi-port key; ``mqtt_psk_port`` still works."""

    def test_ports_list_yields_every_port(self, tmp_path):
        config = load_config(_write_config(tmp_path, _BASE + "mqtt_psk_ports: [8886, 8883]\n"))

        assert config.listen_ports == [8886, 8883]

    def test_legacy_single_port_key_still_supported(self, tmp_path):
        """The deployed add-on writes mqtt_psk_port: 8886 — it must keep working."""
        config = load_config(_write_config(tmp_path, _BASE + "mqtt_psk_port: 8886\n"))

        assert config.listen_ports == [8886]
        assert config.mqtt_psk_port == 8886

    def test_both_keys_are_merged_and_deduped_in_order(self, tmp_path):
        config = load_config(
            _write_config(tmp_path, _BASE + "mqtt_psk_port: 8886\nmqtt_psk_ports: [8883, 8886]\n")
        )

        assert config.listen_ports == [8886, 8883]

    def test_no_port_key_at_all_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match="mqtt_psk_port"):
            load_config(_write_config(tmp_path, _BASE))


class TestServerBindsGivenPort:
    """``PskMqttServer`` binds the port it is handed, not a hardcoded one."""

    def _config(self, port: int) -> BridgeConfig:
        return BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=port,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
        )

    def test_bind_uses_the_port_argument(self):
        """A real socket on a real port — the bound port must be the one passed."""
        port = _free_port()
        server = PskMqttServer(
            config=self._config(8886),
            psk_hint=b"hint",
            event_callback=lambda device_id, event: None,
            port=port,
        )

        sock = server._bind()
        try:
            assert sock.getsockname()[1] == port
        finally:
            sock.close()

    def test_port_defaults_to_the_config_primary_port(self):
        port = _free_port()
        server = PskMqttServer(
            config=self._config(port),
            psk_hint=b"hint",
            event_callback=lambda device_id, event: None,
        )

        sock = server._bind()
        try:
            assert sock.getsockname()[1] == port
        finally:
            sock.close()


class TestRuntimeServersPerPort:
    """The runtime runs one listener per configured port."""

    def _config(self, ports: list[int]) -> BridgeConfig:
        return BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=ports[0],
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            additional_psk_ports=ports[1:],
        )

    def test_one_server_per_listen_port(self):
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime

        runtime = BridgeRuntime(config=self._config([8886, 8883]), psk_hint=b"hint")
        servers = runtime.build_servers()

        assert [server.port for server in servers] == [8886, 8883]

    def test_servers_share_the_same_device_map(self):
        """Both listeners must resolve the same devices — one config, two ports."""
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime

        runtime = BridgeRuntime(config=self._config([8886, 8883]), psk_hint=b"hint")
        servers = runtime.build_servers()

        assert servers[0]._device_map == servers[1]._device_map

    def test_stop_closes_every_server(self):
        """Two real listeners on real ports; after stop() both are unbound."""
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime

        ports = [_free_port(), _free_port()]
        runtime = BridgeRuntime(config=self._config(ports), psk_hint=b"hint")
        runtime.start_servers()

        assert all(server.running for server in runtime.servers)

        runtime.stop_servers()

        assert not any(server.running for server in runtime.servers)
        for port in ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))


class TestPskVariantIsolation:
    """A lab PSK variant must never reach the port that already works.

    The door sensors live on the primary port. Experiments on the 8883/18883
    listener are only safe if that isolation is structural, not remembered.
    """

    def _config(self, variant: str | None) -> BridgeConfig:
        return BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=8886,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            additional_psk_ports=[18883],
            psk_variant=variant,
        )

    def test_primary_port_keeps_the_proven_derivation(self):
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime
        from tuya_psk_bridge.psk_frontend import derive_psk

        runtime = BridgeRuntime(config=self._config("tail32"), psk_hint=b"hint")
        servers = {server.port: server for server in runtime.build_servers()}

        assert servers[8886]._psk_deriver is derive_psk

    def test_additional_port_uses_the_variant(self):
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime
        from tuya_psk_bridge.psk_frontend import PSK_VARIANTS

        runtime = BridgeRuntime(config=self._config("tail32"), psk_hint=b"hint")
        servers = {server.port: server for server in runtime.build_servers()}

        assert servers[18883]._psk_deriver is PSK_VARIANTS["tail32"]

    def test_no_variant_means_every_port_is_proven(self):
        from tuya_psk_bridge.mqtt_runtime import BridgeRuntime
        from tuya_psk_bridge.psk_frontend import derive_psk

        runtime = BridgeRuntime(config=self._config(None), psk_hint=b"hint")

        assert all(server._psk_deriver is derive_psk for server in runtime.build_servers())

    def test_unknown_variant_is_rejected_at_load(self, tmp_path):
        with pytest.raises(ConfigError, match="Unknown PSK variant"):
            load_config(
                _write_config(
                    tmp_path, _BASE + "mqtt_psk_port: 8886\npsk_variant: nonsense\n"
                )
            )

    def test_variants_actually_differ_from_the_default(self):
        """A variant that returns the same bytes would waste a test cycle."""
        from tuya_psk_bridge.psk_frontend import DEFAULT_HINT, PSK_VARIANTS, derive_psk

        identity = bytes.fromhex(
            "03514d18c063bd8a22e8fde0faede3209c7bc350dd6af304"
            "3ba2819d801530a08baa7e05e37646cd4de8d73fb6576be5ea"
        )
        baseline = derive_psk(identity, DEFAULT_HINT)

        for name, deriver in PSK_VARIANTS.items():
            if name == "default":
                continue
            assert deriver(identity, DEFAULT_HINT) != baseline, name
