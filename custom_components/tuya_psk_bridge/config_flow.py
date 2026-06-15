"""Config flow for Tuya PSK Bridge integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_DPS,
    CONF_LOCAL_KEY,
    CONF_MAPPINGS,
    CONF_NAME,
    CONF_PLATFORM,
    CONF_PROFILE,
    CONF_VALUES,
    DEFAULT_DEVICE_CLASS_DOOR,
    DEFAULT_PLATFORM,
    DOOR_SENSOR,
    DOOR_SENSOR_DEFAULTS,
    PROFILES,
    DOMAIN,
)

# Expected Tuya device ID: hex string, 20 characters
DEVICE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{20}$")


def _is_valid_device_id(device_id: str) -> bool:
    """Check that device_id looks like a valid Tuya hex ID."""
    return bool(DEVICE_ID_PATTERN.match(device_id.strip()))


class TuyaPskBridgeFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya PSK Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        self._devices: list[dict[str, Any]] = []
        self._current_device: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start the config flow. Ask how many devices to add."""
        if user_input is not None:
            return await self.async_step_device()

        return self.async_show_menu(
            step_id="user",
            menu_options=["device"],
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Collect device_id, name, and profile."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()
            if not _is_valid_device_id(device_id):
                errors[CONF_DEVICE_ID] = "invalid_device_id"
            if not errors:
                self._current_device = {
                    CONF_DEVICE_ID: device_id,
                    CONF_NAME: user_input[CONF_NAME].strip(),
                    CONF_PROFILE: user_input[CONF_PROFILE],
                }
                return await self.async_step_local_key()

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): str,
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_PROFILE): SelectSelector(
                        SelectSelectorConfig(
                            options=PROFILES,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_local_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Collect local_key (password field)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            local_key = user_input[CONF_LOCAL_KEY].strip()
            if not local_key:
                errors[CONF_LOCAL_KEY] = "empty_local_key"
            if not errors:
                self._current_device[CONF_LOCAL_KEY] = local_key
                return await self.async_step_mappings()

        return self.async_show_form(
            step_id="local_key",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCAL_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"device_name": self._current_device.get(CONF_NAME, "")},
        )

    async def async_step_mappings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Configure DPS mappings. Auto-populate for known profiles."""
        errors: dict[str, str] = {}

        # Pre-populate defaults based on profile
        profile = self._current_device.get(CONF_PROFILE, "")
        if profile == DOOR_SENSOR:
            defaults = {
                CONF_DPS: DOOR_SENSOR_DEFAULTS[CONF_DPS],
                CONF_PLATFORM: DOOR_SENSOR_DEFAULTS[CONF_PLATFORM],
                CONF_DEVICE_CLASS: DOOR_SENSOR_DEFAULTS[CONF_DEVICE_CLASS],
            }
        else:
            defaults = {
                CONF_DPS: "",
                CONF_PLATFORM: DEFAULT_PLATFORM,
                CONF_DEVICE_CLASS: DEFAULT_DEVICE_CLASS_DOOR,
            }

        if user_input is not None:
            mapping = {
                CONF_DPS: user_input[CONF_DPS].strip(),
                CONF_PLATFORM: user_input[CONF_PLATFORM],
                CONF_DEVICE_CLASS: user_input[CONF_DEVICE_CLASS],
                CONF_VALUES: DOOR_SENSOR_DEFAULTS[CONF_VALUES] if profile == DOOR_SENSOR else {},
            }
            self._current_device[CONF_MAPPINGS] = [mapping]
            self._devices.append(self._current_device)
            self._current_device = {}

            # Ask if user wants to add another device
            return await self.async_step_add_more()

        return self.async_show_form(
            step_id="mappings",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DPS, default=defaults[CONF_DPS]): str,
                    vol.Required(CONF_PLATFORM, default=defaults[CONF_PLATFORM]): str,
                    vol.Required(
                        CONF_DEVICE_CLASS, default=defaults[CONF_DEVICE_CLASS]
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_add_more(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Optionally add another device or finish."""
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_device()

            # Finalize
            return self.async_create_entry(
                title=f"Tuya PSK Bridge ({len(self._devices)} device(s))",
                data={CONF_DEVICES: self._devices},
            )

        return self.async_show_menu(
            step_id="add_more",
            menu_options=["add_more_device", "finish"],
        )

    async def async_step_add_more_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """User chose to add another device."""
        return await self.async_step_device()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """User chose to finish adding devices."""
        return self.async_create_entry(
            title=f"Tuya PSK Bridge ({len(self._devices)} device(s))",
            data={CONF_DEVICES: self._devices},
        )


class TuyaPskBridgeOptionsFlowHandler(OptionsFlow):
    """Handle options flow for reconfiguring existing devices.

    MVP: options flow is not implemented. Users should reconfigure via
    the main config flow (delete and re-add the integration).
    Full options flow support is a roadmap item.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Options flow entry point. Deferred to roadmap."""
        return self.async_abort(reason="reconfigure_via_config_flow")
