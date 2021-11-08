from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    CURRENT_HVAC_COOL,
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    CURRENT_HVAC_OFF,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    PRESET_AWAY,
    PRESET_NONE,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_UNIQUE_ID,
    EVENT_HOMEASSISTANT_START,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
)

# class defaults_thermostat:

# DEFAULT_NAME = "MultiZone Thermostat"
DEFAULT_TARGET_TEMP_HEAT = 19.0
DEFAULT_TARGET_TEMP_COOL = 28.0
DEFAULT_MAX_TEMP_HEAT = 24
DEFAULT_MIN_TEMP_HEAT = 17
DEFAULT_MAX_TEMP_COOL = 35
DEFAULT_MIN_TEMP_COOL = 20
DEFAULT_AREA = 0
DEFAULT_INITIAL_HVAC_MODE = HVAC_MODE_OFF
DEFAULT_INITIAL_PRESET_MODE = PRESET_NONE
DEFAULT_PASSIVE_SWITCH = False

DEFAULT_OLD_STATE = False
DEFAULT_RESTORE_PARAMETERS = False
DEFAULT_RESTORE_INTEGRAL = False

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE

SUPPORTED_HVAC_MODES = [HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_OFF]


# class defaults_controller_input:
# on_off mode
DEFAULT_HYSTERESIS_TOLERANCE = 0.5

# PWM/PID controller
DEFAULT_DIFFERENCE = 100
DEFAULT_MIN_DIFF = 0
DEFAULT_PWM = 0

DEFAULT_SENSOR_FILTER = 0

CONF_SENSOR = "sensor"
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_INITIAL_PRESET_MODE = "initial_preset_mode"

CONF_PASSIVE_SWITCH_CHECK = "passive_switch_check"

# only requied for hvac_Settings
# class defaults_controller:
CONF_HVAC_MODE_INIT_TEMP = "initial_target_temp"
CONF_HVAC_MODE_MIN_TEMP = "min_temp"
CONF_HVAC_MODE_MAX_TEMP = "max_temp"
CONF_AWAY_TEMP = "away_temp"

CONF_PRECISION = "precision"
CONF_AREA = "room_area"
CONF_ENABLE_OLD_STATE = "restore_from_old_state"
CONF_ENABLE_OLD_PARAMETERS = "restore_parameters"
CONF_ENABLE_OLD_INTEGRAL = "restore_integral"
CONF_STALE_DURATION = "sensor_stale_duration"

CONF_PASSIVE_SWITCH_DURATION = "passive_switch_duration"

# proportional valve control (pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

# on_off thermostat
CONF_ON_OFF_MODE = "on_off_mode"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_KEEP_ALIVE = "keep_alive"
CONF_HYSTERESIS_TOLERANCE_ON = "hysteresis_tolerance_on"
CONF_HYSTERESIS_TOLERANCE_OFF = "hysteresis_tolerance_off"

# proportional mode
CONF_PROPORTIONAL_MODE = "proportional_mode"
CONF_PWM = "pwm"
CONF_CONTROL_REFRESH_INTERVAL = "control_interval"
CONF_DIFFERENCE = "difference"
CONF_MIN_DIFFERENCE = "min_difference"
CONF_MAX_DIFFERENCE = "max_difference"
CONF_MIN_DIFF = "minimal_diff"

CONF_SENSOR_FILTER = "sensor_filter"

# PID controller
CONF_PID_MODE = "PID_mode"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_D_AVG = "derative_avg"

# weather compensating mode
CONF_WC_MODE = "weather_mode"
CONF_SENSOR_OUT = "sensor_out"
CONF_KA = "ka"
CONF_KB = "kb"

# Master mode
CONF_MASTER_MODE = "MASTER_mode"
CONF_SATELITES = "satelites"
# valve_control_mode
# CONF_VALVE_MODE = "PID_VALVE_mode"
CONF_GOAL = "goal"

SUPPORTED_PRESET_MODES = [
    PRESET_NONE,
    PRESET_AWAY,
]
