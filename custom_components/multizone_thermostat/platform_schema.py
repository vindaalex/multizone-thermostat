"""Constants used for multizone thermostat."""
import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, PRESET_NONE, HVACMode
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_UNIQUE_ID,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
)
import homeassistant.helpers.config_validation as cv

from . import validations as val
from .const import (
    CONF_AREA,
    CONF_CONTINUOUS_LOWER_LOAD,
    CONF_CONTROL_REFRESH_INTERVAL,
    CONF_DETAILED_OUTPUT,
    CONF_ENABLE_OLD_INTEGRAL,
    CONF_ENABLE_OLD_PARAMETERS,
    CONF_ENABLE_OLD_STATE,
    CONF_EXTRA_PRESETS,
    CONF_FILTER_MODE,
    CONF_HYSTERESIS_TOLERANCE_OFF,
    CONF_HYSTERESIS_TOLERANCE_ON,
    CONF_INCLUDE_VALVE_LAG,
    CONF_INITIAL_HVAC_MODE,
    CONF_INITIAL_PRESET_MODE,
    CONF_KA,
    CONF_KB,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_MASTER_MODE,
    CONF_MASTER_OPERATION_MODE,
    CONF_MASTER_SCALE_BOUND,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_VALVE,
    CONF_ON_OFF_MODE,
    CONF_PASSIVE_CHECK_TIME,
    CONF_PASSIVE_SWITCH_CHECK,
    CONF_PASSIVE_SWITCH_DURATION,
    CONF_PASSIVE_SWITCH_OPEN_TIME,
    CONF_PID_MODE,
    CONF_PRECISION,
    CONF_PROPORTIONAL_MODE,
    CONF_PWM_DURATION,
    CONF_PWM_RESOLUTION,
    CONF_PWM_SCALE,
    CONF_PWM_SCALE_HIGH,
    CONF_PWM_SCALE_LOW,
    CONF_PWM_THRESHOLD,
    CONF_SATELITES,
    CONF_SENSOR,
    CONF_SENSOR_OUT,
    CONF_STALE_DURATION,
    CONF_SWITCH_MODE,
    CONF_TARGET_TEMP_INIT,
    CONF_TARGET_TEMP_MAX,
    CONF_TARGET_TEMP_MIN,
    CONF_WC_MODE,
    CONF_WINDOW_OPEN_TEMPDROP,
    DEFAULT_AREA,
    DEFAULT_DETAILED_OUTPUT,
    DEFAULT_INCLUDE_VALVE_LAG,
    DEFAULT_MASTER_SCALE_BOUND,
    DEFAULT_MAX_TEMP_COOL,
    DEFAULT_MAX_TEMP_HEAT,
    DEFAULT_MIN_DIFF,
    DEFAULT_MIN_LOAD,
    DEFAULT_MIN_TEMP_COOL,
    DEFAULT_MIN_TEMP_HEAT,
    DEFAULT_MIN_VALVE_PWM,
    DEFAULT_OLD_STATE,
    DEFAULT_OPERATION,
    DEFAULT_PASSIVE_CHECK_TIME,
    DEFAULT_PASSIVE_SWITCH,
    DEFAULT_PASSIVE_SWITCH_OPEN_TIME,
    DEFAULT_PWM,
    DEFAULT_PWM_RESOLUTION,
    DEFAULT_PWM_SCALE,
    DEFAULT_RESTORE_INTEGRAL,
    DEFAULT_RESTORE_PARAMETERS,
    DEFAULT_SENSOR_FILTER,
    DEFAULT_TARGET_TEMP_COOL,
    DEFAULT_TARGET_TEMP_HEAT,
    NC_SWITCH_MODE,
    NO_SWITCH_MODE,
    NestingMode,
    OperationMode,
)

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
    vol.Optional(
        CONF_PASSIVE_SWITCH_OPEN_TIME, default=DEFAULT_PASSIVE_SWITCH_OPEN_TIME
    ): vol.All(cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_EXTRA_PRESETS, default={}): vol.Schema(dict),
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
    vol.Optional(
        CONF_MASTER_SCALE_BOUND, default=DEFAULT_MASTER_SCALE_BOUND
    ): cv.positive_float,
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
                [
                    NestingMode.MASTER_BALANCED,
                    NestingMode.MASTER_MIN_ON,
                    NestingMode.MASTER_CONTINUOUS,
                ]
            ),
            vol.Optional(
                CONF_INCLUDE_VALVE_LAG, default=DEFAULT_INCLUDE_VALVE_LAG
            ): vol.All(cv.time_period, cv.positive_timedelta),
            **controller_config,
            vol.Optional(
                CONF_CONTINUOUS_LOWER_LOAD, default=DEFAULT_MIN_LOAD
            ): vol.Coerce(float),
            vol.Optional(CONF_MIN_VALVE, default=DEFAULT_MIN_VALVE_PWM): vol.Coerce(
                float
            ),
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
    val.validate_initial_hvac_mode(),
    val.validate_initial_preset_mode(),
    val.validate_initial_control_mode(),
    val.validate_initial_sensors(),
    val.validate_window(),
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
            vol.Optional(
                CONF_PASSIVE_CHECK_TIME, default=DEFAULT_PASSIVE_CHECK_TIME
            ): vol.Datetime(format="%H:%M"),
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
    val.validate_stuck_time(),
)
