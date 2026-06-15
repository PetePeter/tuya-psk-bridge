"""Smoke tests for the CLI entrypoint."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from tuya_psk_bridge.main import _build_parser, main
from tuya_psk_bridge.models import BridgeConfig, DeviceConfig
from tuya_psk_bridge.mqtt_runtime import BridgeRuntime, _wait_for


class TestBuildParser:
    """Tests for the argument parser."""

    def test_parser_requires_config(self) -> None:
        """Parser should reject invocation without --config."""
        parser = _build_parser()
        try:
            parser.parse_args([])
            assert False, "Should have raised SystemExit"
        except SystemExit:
            pass  # Expected

    def test_parser_accepts_config(self) -> None:
        """Parser should accept --config path."""
        parser = _build_parser()
        result = parser.parse_args(["--config", "/tmp/bridge.yaml"])
        assert result.config == "/tmp/bridge.yaml"

    def test_parser_accepts_log_level(self) -> None:
        """Parser should accept optional --log-level."""
        parser = _build_parser()
        result = parser.parse_args(["--config", "/tmp/bridge.yaml", "--log-level", "DEBUG"])
        assert result.log_level == "DEBUG"


class TestMainCallable:
    """Tests that main() is callable and handles errors gracefully."""

    def test_main_returns_1_on_missing_config(self) -> None:
        """main() should return exit code 1 when config file doesn't exist."""
        assert main(["--config", "/nonexistent/path.yaml"]) == 1

    def test_main_returns_1_on_invalid_yaml(self, tmp_path: Path) -> None:
        """main() should return exit code 1 when config YAML is invalid."""
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("listen_host: 0.0.0.0\n")  # missing required fields
        assert main(["--config", str(bad_config)]) == 1

    def test_main_returns_0_on_interrupt(self, tmp_path: Path) -> None:
        """main() should return 0 when interrupted before server start."""
        config = tmp_path / "test.yaml"
        config.write_text(
            "listen_host: 0.0.0.0\n"
            "mqtt_psk_port: 18883\n"
            "ha_mqtt_host: 127.0.0.1\n"
            "ha_mqtt_port: 1883\n"
            "log_level: INFO\n"
            "devices: []\n"
        )
        with patch("tuya_psk_bridge.main.BridgeRuntime") as MockRuntime:
            mock_instance = MagicMock()
            MockRuntime.return_value = mock_instance
            mock_instance._server = MagicMock()
            mock_instance._server._thread = MagicMock()
            # Simulate KeyboardInterrupt during start
            mock_instance.start.side_effect = KeyboardInterrupt()
            mock_instance.stop = MagicMock()
            result = main(["--config", str(config)])
            mock_instance.stop.assert_called_once()


class TestWaitForPredicate:
    """Verify _wait_for uses a callable predicate, not a bare bool."""

    def test_wait_for_accepts_callable(self) -> None:
        """_wait_for should accept a lambda, not raise TypeError on bool."""
        # lambda: True returns immediately
        assert _wait_for(lambda: True, timeout=0.1) is True

    def test_wait_for_times_out(self) -> None:
        """_wait_for should return False when predicate stays false."""
        assert _wait_for(lambda: False, timeout=0.1, poll_interval=0.05) is False

    def test_mqtt_runtime_start_uses_callable_predicate(self) -> None:
        """BridgeRuntime.start must pass a callable to _wait_for, not self._publisher._connected."""
        source = inspect.getsource(
            __import__("tuya_psk_bridge.mqtt_runtime", fromlist=["BridgeRuntime"]).BridgeRuntime.start
        )
        # Must contain lambda (callable) reference, not bare _connected
        assert "lambda:" in source
        assert "self._publisher._connected" not in source


class TestBridgeRuntimeAvailability:
    def test_start_publishes_online_availability(self) -> None:
        device = DeviceConfig(
            device_id="0123456789abcdefabcd",
            local_key="abcdef0123456789",
            name="Door",
            profile="door_sensor",
        )
        config = BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=18883,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            devices=[device],
        )

        with patch("tuya_psk_bridge.mqtt_runtime.HaMqttPublisher") as publisher_cls, patch(
            "tuya_psk_bridge.mqtt_runtime.PskMqttServer"
        ) as server_cls:
            publisher = publisher_cls.return_value
            publisher.connected = True

            runtime = BridgeRuntime(config, psk_hint=b"hint")
            runtime.start()

            publisher.publish_availability.assert_called_once_with(device, True)
            server_cls.return_value.start.assert_called_once()

    def test_stop_publishes_offline_availability(self) -> None:
        device = DeviceConfig(
            device_id="0123456789abcdefabcd",
            local_key="abcdef0123456789",
            name="Door",
            profile="door_sensor",
        )
        config = BridgeConfig(
            listen_host="127.0.0.1",
            mqtt_psk_port=18883,
            ha_mqtt_host="127.0.0.1",
            ha_mqtt_port=1883,
            ha_mqtt_username=None,
            ha_mqtt_password=None,
            log_level="INFO",
            devices=[device],
        )

        with patch("tuya_psk_bridge.mqtt_runtime.HaMqttPublisher") as publisher_cls:
            publisher = publisher_cls.return_value
            runtime = BridgeRuntime(config, psk_hint=b"hint")
            runtime.stop()

            publisher.publish_availability.assert_called_once_with(device, False)
            publisher.disconnect.assert_called_once()
