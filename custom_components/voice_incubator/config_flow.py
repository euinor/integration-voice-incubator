"""Config flow for Voice Incubator: one entry, nothing to ask.

The voice_incubator app starts this flow itself when it finds the integration
installed but not set up; it can also be added from the UI. Its options are
the timer sensors' (sensor.py).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_EXPOSE_TIMER_SENSORS,
    CONF_RETENTION_MINUTES,
    DEFAULT_EXPOSE_TIMER_SENSORS,
    DEFAULT_RETENTION_MINUTES,
    DOMAIN,
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EXPOSE_TIMER_SENSORS, default=DEFAULT_EXPOSE_TIMER_SENSORS): BooleanSelector(),
        vol.Required(CONF_RETENTION_MINUTES, default=DEFAULT_RETENTION_MINUTES): vol.All(
            NumberSelector(
                NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX)
            ),
            vol.Coerce(int),
        ),
    }
)


class VoiceIncubatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Voice Incubator."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Voice Incubator", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VoiceIncubatorOptionsFlow()


class VoiceIncubatorOptionsFlow(OptionsFlow):
    """The timer sensors' options. Applied without a reload, which would lose
    every timer."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, self.config_entry.options),
        )
