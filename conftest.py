"""Root conftest: set up sys.path and mock HA for isolated test runs."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Ensure bridge package is importable.
_bridge_root = str(Path(__file__).resolve().parent / "bridge")
if _bridge_root not in sys.path:
    sys.path.insert(0, _bridge_root)

# Also ensure project root is importable for custom_components.
_project_root = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _install_ha_mocks() -> None:
    """Create mock modules for all HA imports used by the integration."""
    if "homeassistant" in sys.modules:
        return

    ha = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")
    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha_helpers_selector = types.ModuleType("homeassistant.helpers.selector")
    ha_components = types.ModuleType("homeassistant.components")
    ha_components_mqtt = types.ModuleType("homeassistant.components.mqtt")

    ha_core.HomeAssistant = MagicMock
    ha_config_entries.ConfigEntry = MagicMock
    ha_config_entries.ConfigFlow = MagicMock
    ha_config_entries.OptionsFlow = MagicMock
    ha_config_entries.ConfigFlowResult = dict
    ha_data_entry_flow.FlowResult = dict
    ha_helpers_selector.SelectSelector = MagicMock
    ha_helpers_selector.SelectSelectorConfig = MagicMock
    ha_helpers_selector.SelectSelectorMode = MagicMock
    ha_helpers_selector.TextSelector = MagicMock
    ha_helpers_selector.TextSelectorConfig = MagicMock
    ha_helpers_selector.TextSelectorType = MagicMock

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.config_entries"] = ha_config_entries
    sys.modules["homeassistant.data_entry_flow"] = ha_data_entry_flow
    sys.modules["homeassistant.helpers"] = ha_helpers
    sys.modules["homeassistant.helpers.selector"] = ha_helpers_selector
    sys.modules["homeassistant.components"] = ha_components
    sys.modules["homeassistant.components.mqtt"] = ha_components_mqtt


_install_ha_mocks()
