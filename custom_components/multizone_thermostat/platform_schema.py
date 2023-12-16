import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_UNIQUE_ID,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
)
from homeassistant.components.climate import PLATFORM_SCHEMA, PRESET_NONE

from .const import *
from .validations import *

SUPPORTED_MASTER_MODES = [MASTER_CONTINUOUS, MASTER_BALANCED, MASTER_MIN_ON]
SUPPORTED_HVAC_MODES = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]


# Configuration of thermostats
hvac_control_options = {
    vol.Required(CONF_ENTITY_ID): cv.entity_id,  # switch to control
    vol.Optional(CONF_SWITCH_MODE, default=NC_SWITCH_MODE): vol.In(
        [NC_SWITCH_MODE, NO_SWITCH_MODE]
    ),
    vol.Optional(CONF_PASSIVE_SWITCH_DURATION): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    vol.Optional(CONF_PASSIVE_SWITCH_OPEN_TIME, default=DEFAULT_PASSIVE_SWITCH_OPEN_TIME): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    #TODO multiple presets
    vol.Optional(CONF_EXTRA_PRESETS,default={}):vol.Schema(dict),
}

controller_config = {
    vol.Required(CONF_CONTROL_REFRESH_INTERVAL): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    vol.Optional(CONF_PWM_DURATION, default=DEFAULT_PWM): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    vol.Optional(CONF_PWM_SCALE, default=DEFAULT_PWM_SCALE): vol.Coerce(float),
    vol.Optional(CONF_PWM_RESOLUTION, default=DEFAULT_PWM_RESOLUTION): vol.Coerce(
        float
    ),
    vol.Optional(CONF_PWM_THRESHOLD, default=DEFAULT_MIN_DIFF): vol.Coerce(float),
}


PID_control_options = {
    vol.Required(CONF_KP): vol.Coerce(float),
    vol.Required(CONF_KI): vol.Coerce(float),
    vol.Required(CONF_KD): vol.Coerce(float),
    vol.Optional(CONF_PWM_SCALE_LOW): vol.Coerce(float),
    vol.Optional(CONF_PWM_SCALE_HIGH): vol.Coerce(float),
    vol.Optional(CONF_WINDOW_OPEN_TEMPDROP): vol.Coerce(float),
}

WC_control_options = {
    vol.Required(CONF_KA): vol.Coerce(float),
    vol.Required(CONF_KB): vol.Coerce(float),
    vol.Optional(CONF_PWM_SCALE_LOW): vol.Coerce(float),
    vol.Optional(CONF_PWM_SCALE_HIGH): vol.Coerce(float),
}

VALVE_control_options_req = {
    vol.Required(CONF_GOAL): vol.Coerce(float),
    vol.Required(CONF_KP): vol.Coerce(float),
    vol.Required(CONF_KI): vol.Coerce(float),
    vol.Required(CONF_KD): vol.Coerce(float),
}

# on_off
on_off = {
    vol.Required(CONF_HYSTERESIS_TOLERANCE_ON): vol.Coerce(float),
    vol.Required(CONF_HYSTERESIS_TOLERANCE_OFF): vol.Coerce(float),
    vol.Optional(CONF_MIN_CYCLE_DURATION): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    vol.Optional(CONF_CONTROL_REFRESH_INTERVAL): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
}

temp_set_heat = {
    vol.Optional(CONF_TARGET_TEMP_MIN, default=DEFAULT_MIN_TEMP_HEAT): vol.Coerce(
        float
    ),
    vol.Optional(CONF_TARGET_TEMP_MAX, default=DEFAULT_MAX_TEMP_HEAT): vol.Coerce(
        float
    ),
    vol.Optional(CONF_TARGET_TEMP_INIT, default=DEFAULT_TARGET_TEMP_HEAT): vol.Coerce(
        float
    ),
}

temp_set_cool = {
    vol.Optional(CONF_TARGET_TEMP_MIN, default=DEFAULT_MIN_TEMP_COOL): vol.Coerce(
        float
    ),
    vol.Optional(CONF_TARGET_TEMP_MAX, default=DEFAULT_MAX_TEMP_COOL): vol.Coerce(
        float
    ),
    vol.Optional(CONF_TARGET_TEMP_INIT, default=DEFAULT_TARGET_TEMP_COOL): vol.Coerce(
        float
    ),
}

on_off_heat = {vol.Optional(CONF_ON_OFF_MODE): vol.Schema({**on_off})}
on_off_cool = {vol.Optional(CONF_ON_OFF_MODE): vol.Schema({**on_off})}

# proportional mode"
prop = {
    **controller_config,
    vol.Optional(CONF_PID_MODE): vol.Schema(PID_control_options),
    vol.Optional(CONF_WC_MODE): vol.Schema(WC_control_options),
}

prop_heat = {vol.Optional(CONF_PROPORTIONAL_MODE): vol.Schema({**prop})}
prop_cool = {vol.Optional(CONF_PROPORTIONAL_MODE): vol.Schema({**prop})}

master = {
    vol.Optional(CONF_MASTER_MODE): vol.Schema(
        {
            vol.Required(CONF_SATELITES): cv.ensure_list,
            vol.Optional(CONF_MASTER_OPERATION_MODE, default=DEFAULT_OPERATION): vol.In(
                SUPPORTED_MASTER_MODES
            ),
            **controller_config,
            vol.Optional(
                CONF_CONTINUOUS_LOWER_LOAD, default=DEFAULT_MIN_LOAD
            ): vol.Coerce(float),
            vol.Optional(CONF_MIN_VALVE, default=DEFAULT_MIN_VALVE_PWM): vol.Coerce(
                float
            ),
            vol.Optional(CONF_VALVE_MODE): vol.Schema(VALVE_control_options_req),
        }
    )
}

hvac_control_heat = {
    **hvac_control_options,
    **temp_set_heat,
    **on_off_heat,
    **prop_heat,
    **master,
}

hvac_control_cool = {
    **hvac_control_options,
    **temp_set_cool,
    **on_off_cool,
    **prop_cool,
    **master,
}

PLATFORM_SCHEMA = vol.All(
    cv.has_at_least_one_key(HVACMode.HEAT, HVACMode.COOL),
    validate_initial_hvac_mode(),
    validate_initial_preset_mode(),
    validate_initial_control_mode(),
    validate_initial_sensors(),
    validate_window(),
    PLATFORM_SCHEMA.extend(
        {
            vol.Optional(CONF_NAME, default=OperationMode.MASTER): cv.string,
            vol.Optional(CONF_UNIQUE_ID): cv.string,
            vol.Optional(CONF_SENSOR): cv.entity_id,
            vol.Optional(CONF_FILTER_MODE, default=DEFAULT_SENSOR_FILTER): vol.Coerce(
                int
            ),
            vol.Optional(CONF_SENSOR_OUT): cv.entity_id,
            vol.Optional(CONF_INITIAL_HVAC_MODE, default=HVACMode.OFF): vol.In(
                SUPPORTED_HVAC_MODES
            ),
            vol.Optional(CONF_INITIAL_PRESET_MODE, default=PRESET_NONE): cv.string,
            vol.Optional(CONF_PRECISION): vol.In(
                [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
            ),
            vol.Optional(CONF_AREA, default=DEFAULT_AREA): vol.Coerce(float),
            vol.Optional(
                CONF_DETAILED_OUTPUT, default=DEFAULT_DETAILED_OUTPUT
            ): cv.boolean,
            vol.Optional(CONF_STALE_DURATION): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
            vol.Optional(
                CONF_PASSIVE_SWITCH_CHECK, default=DEFAULT_PASSIVE_SWITCH
            ): cv.boolean,
            vol.Optional(CONF_ENABLE_OLD_STATE, default=DEFAULT_OLD_STATE): cv.boolean,
            vol.Optional(
                CONF_ENABLE_OLD_PARAMETERS, default=DEFAULT_RESTORE_PARAMETERS
            ): cv.boolean,
            vol.Optional(
                CONF_ENABLE_OLD_INTEGRAL, default=DEFAULT_RESTORE_INTEGRAL
            ): cv.boolean,
            vol.Optional(str(HVACMode.HEAT)): vol.Schema(hvac_control_heat),
            vol.Optional(str(HVACMode.COOL)): vol.Schema(hvac_control_cool),
        }
    ),
)
