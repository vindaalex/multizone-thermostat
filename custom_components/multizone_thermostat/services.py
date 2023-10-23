import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    PRESET_AWAY,
    PRESET_NONE,
    HVACMode,
)

from .const import *

SUPPORTED_PRESET_MODES = [PRESET_NONE, PRESET_AWAY]
SUPPORTED_HVAC_MODES = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]


def register_services():
    platform = entity_platform.current_platform.get()
    assert platform

    platform.async_register_entity_service(  # type: ignore
        "set_preset_mode",
        {vol.Required(ATTR_PRESET_MODE): vol.In(SUPPORTED_PRESET_MODES)},
        "async_set_preset_mode",
    )

    platform.async_register_entity_service(  # type: ignore
        "detailed_output",
        {
            vol.Required(ATTR_HVAC_MODE): vol.In(SUPPORTED_HVAC_MODES),
            vol.Required("new_mode"): cv.boolean,
        },
        "set_detailed_output",
    )

    platform.async_register_entity_service(  # type: ignore
        "pwm_threshold",
        {
            vol.Required(ATTR_HVAC_MODE): vol.In(SUPPORTED_HVAC_MODES),
            vol.Required("new_threshold"): vol.Coerce(float),
        },
        "async_set_pwm_threshold",
    )

    platform.async_register_entity_service(  # type: ignore
        "set_pid",
        {
            vol.Required(ATTR_HVAC_MODE): vol.In(SUPPORTED_HVAC_MODES),
            vol.Optional(ATTR_KP): vol.Coerce(float),
            vol.Optional(ATTR_KI): vol.Coerce(float),
            vol.Optional(ATTR_KD): vol.Coerce(float),
            vol.Optional("update", default=True): vol.Boolean,
        },
        "async_set_pid",
    )

    platform.async_register_entity_service(  # type: ignore
        "set_filter_mode",
        {
            vol.Optional("mode"): vol.Coerce(int),
        },
        "async_set_filter_mode",
    )

    platform.async_register_entity_service(  # type: ignore
        "set_integral",
        {
            vol.Required(ATTR_HVAC_MODE): vol.In(SUPPORTED_HVAC_MODES),
            vol.Required("integral"): vol.Coerce(float),
        },
        "async_set_integral",
    )
    platform.async_register_entity_service(  # type: ignore
        "set_goal",
        {
            vol.Required(ATTR_HVAC_MODE): vol.In(SUPPORTED_HVAC_MODES),
            vol.Required("goal"): vol.Coerce(float),
        },
        "async_set_goal",
    )
    platform.async_register_entity_service(  # type: ignore
        "set_ka_kb",
        {
            vol.Required(ATTR_HVAC_MODE): vol.In(SUPPORTED_HVAC_MODES),
            vol.Optional(ATTR_KA): vol.Coerce(float),
            vol.Optional(ATTR_KB): vol.Coerce(float),
        },
        "async_set_ka_kb",
    )

    platform.async_register_entity_service(  # type: ignore
        "satelite_mode",
        {
            vol.Required(ATTR_CONTROL_MODE): vol.Coerce(OperationMode),
            vol.Optional(ATTR_CONTROL_OFFSET): vol.Coerce(float),
            vol.Optional("sat_id"): vol.Coerce(int),
            vol.Optional("pwm_start_time"): vol.Coerce(float),
        },
        "async_set_satelite_mode",
    )
