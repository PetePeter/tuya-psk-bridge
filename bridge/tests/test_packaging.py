"""Packaging validation tests.

Verify Dockerfile, add-on config, and package structure assumptions
without requiring Docker or a live HA instance.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE_ROOT = PROJECT_ROOT / "bridge"
ADDON_ROOT = PROJECT_ROOT / "addon"


class TestDockerfile:
    """Validate Dockerfile assumptions."""

    def test_dockerfile_exists(self) -> None:
        assert (PROJECT_ROOT / "addon" / "Dockerfile").exists()

    def test_dockerfile_installs_jq(self) -> None:
        """Dockerfile must install jq since run.sh depends on it."""
        dockerfile = (PROJECT_ROOT / "addon" / "Dockerfile").read_text()
        assert "jq" in dockerfile

    def test_dockerfile_copies_package_before_pip_install(self) -> None:
        """Package source must be copied before pip install."""
        dockerfile = (PROJECT_ROOT / "addon" / "Dockerfile").read_text()
        lines = dockerfile.splitlines()
        copy_pkg = -1
        pip_install = -1
        for i, line in enumerate(lines):
            if "COPY bridge/tuya_psk_bridge" in line or "COPY bridge" in line:
                copy_pkg = i
            if "pip install" in line:
                pip_install = i
        assert copy_pkg < pip_install, (
            "Dockerfile must COPY package source before pip install"
        )

    def test_dockerfile_exposes_psk_port(self) -> None:
        dockerfile = (PROJECT_ROOT / "addon" / "Dockerfile").read_text()
        assert "EXPOSE" in dockerfile


class TestAddonConfig:
    """Validate HA add-on config.yaml structure."""

    def test_addon_config_exists(self) -> None:
        assert (ADDON_ROOT / "config.yaml").exists()

    def test_addon_config_has_devices_schema(self) -> None:
        config = (ADDON_ROOT / "config.yaml").read_text()
        assert "devices:" in config
        assert "device_id:" in config
        assert "local_key:" in config

    def test_addon_config_has_listen_options(self) -> None:
        config = (ADDON_ROOT / "config.yaml").read_text()
        assert "mqtt_psk_port:" in config or "listen_host:" in config


class TestRunSh:
    """Validate add-on entrypoint script."""

    def test_run_sh_exists(self) -> None:
        assert (ADDON_ROOT / "run.sh").exists()

    def test_run_sh_calls_python_main(self) -> None:
        run_sh = (ADDON_ROOT / "run.sh").read_text()
        assert "python -m tuya_psk_bridge.main" in run_sh

    def test_run_sh_references_config_path(self) -> None:
        run_sh = (ADDON_ROOT / "run.sh").read_text()
        assert "--config" in run_sh

    def test_run_sh_has_sigterm_handler(self) -> None:
        run_sh = (ADDON_ROOT / "run.sh").read_text()
        assert "SIGTERM" in run_sh


class TestPackageStructure:
    """Validate bridge package structure."""

    def test_main_module_exists(self) -> None:
        assert (BRIDGE_ROOT / "tuya_psk_bridge" / "main.py").exists()

    def test_pyproject_has_sslpsk(self) -> None:
        pyproject = (BRIDGE_ROOT / "pyproject.toml").read_text()
        assert "sslpsk" in pyproject, (
            "sslpsk must be declared as a dependency in pyproject.toml"
        )

    def test_ha_mqtt_module_exists(self) -> None:
        assert (BRIDGE_ROOT / "tuya_psk_bridge" / "ha_mqtt.py").exists()

    def test_no_runtime_duplicate_publisher(self) -> None:
        """mqtt_runtime.py must not contain its own _HaMqttPublisher."""
        runtime = (BRIDGE_ROOT / "tuya_psk_bridge" / "mqtt_runtime.py").read_text()
        assert "class _HaMqttPublisher" not in runtime, (
            "mqtt_runtime.py should use HaMqttPublisher from ha_mqtt, not define its own"
        )

    def test_runtime_imports_from_ha_mqtt(self) -> None:
        runtime = (BRIDGE_ROOT / "tuya_psk_bridge" / "mqtt_runtime.py").read_text()
        assert "from .ha_mqtt import" in runtime

    def test_no_secrets_in_package(self) -> None:
        """Verify no real-looking secrets in Python source files.

        Catches plausible private values that should never appear in a
        public-repo source file.  Patterns are generic — no actual lab
        values are embedded here.
        """
        patterns = [
            r"password\s*[:=]\s*['\"]",           # hardcoded password assignment
            r"secret\s*[:=]\s*['\"]",             # hardcoded secret assignment
            r"local_key\s*[:=]\s*['\"][^!]",      # local_key not using !secret placeholder
            r"token\s*[:=]\s*['\"]",              # hardcoded token assignment
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}",   # private IP addresses (loose)
        ]
        pkg_dir = BRIDGE_ROOT / "tuya_psk_bridge"
        for py_file in pkg_dir.glob("*.py"):
            content = py_file.read_text()
            for pattern in patterns:
                assert not re.search(pattern, content), (
                    f"Potential secret/real value found in {py_file.name}: {pattern}"
                )
