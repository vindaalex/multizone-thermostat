"""Multizone constants"""
from homeassistant.components.climate import (
    PRESET_NONE,
    PRESET_AWAY,
    HVACMode,
)

# DEFAULT_NAME = "MultiZone Thermostat"
DEFAULT_TARGET_TEMP_HEAT = 19.0
DEFAULT_TARGET_TEMP_COOL = 28.0
DEFAULT_MAX_TEMP_HEAT = 24
DEFAULT_MIN_TEMP_HEAT = 17
DEFAULT_MAX_TEMP_COOL = 35
DEFAULT_MIN_TEMP_COOL = 20
DEFAULT_AREA = 0
DEFAULT_INITIAL_HVAC_MODE = HVACMode.OFF
DEFAULT_INITIAL_PRESET_MODE = PRESET_NONE

NC_SWITCH_MODE = "NC"
NO_SWITCH_MODE = "NO"

DEFAULT_NEST_MATRIX = 20
DEFAULT_MIN_LOAD = 0

DEFAULT_PASSIVE_SWITCH = False

DEFAULT_OLD_STATE = False
DEFAULT_RESTORE_PARAMETERS = False
DEFAULT_RESTORE_INTEGRAL = False

SUPPORTED_HVAC_MODES = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]

CONTROL_OUTPUT = "control_output"

# class defaults_controller_input:
# on_off mode
DEFAULT_HYSTERESIS_TOLERANCE = 0.5

# PWM/PID controller
DEFAULT_PWM_SCALE = 100
DEFAULT_MIN_DIFF = 0
DEFAULT_PWM = 0
DEFAULT_PWM_RESOLUTION = 50
# DEFAULT_VALVE_DELAY = 0
CONF_HVAC_DEFINITION = "hvac_def"
# MASTER
DEFAULT_OPERATION = "on_off"

DEFAULT_SENSOR_FILTER = 0

CONF_SENSOR = "sensor"
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_INITIAL_PRESET_MODE = "initial_preset_mode"
CONF_SWITCH_MODE = "switch_mode"
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
CONF_CONTROL_REFRESH_INTERVAL = "control_interval"

# proportional valve control (pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

# on_off thermostat
CONF_ON_OFF_MODE = "on_off_mode"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_HYSTERESIS_TOLERANCE_ON = "hysteresis_tolerance_on"
CONF_HYSTERESIS_TOLERANCE_OFF = "hysteresis_tolerance_off"

# proportional mode
CONF_PROPORTIONAL_MODE = "proportional_mode"
CONF_PWM = "pwm"

CONF_PWM_SCALE = "pwm_scale"
CONF_MIN_DIFFERENCE = "min_difference"
CONF_MAX_DIFFERENCE = "max_difference"
CONF_MIN_DIFF = "minimal_diff"
CONF_WINDOW_OPEN_TEMPDROP = "window_open_tempdrop"
CONF_PWM_RESOLUTION = "pwm_resolution"
# CONF_VALVE_DELAY = "valve_delay"

CONF_SENSOR_FILTER = "sensor_filter"

# PID controller
CONF_PID_MODE = "PID_mode"
PID_CONTROLLER = "PID_controller"
VALVE_PID_MODE = "pid_valve"
PROP_PID_MODE = "pid_prop"
VALVE_POS = "valve_pos"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"

# weather compensating mode
CONF_WC_MODE = "weather_mode"
CONF_SENSOR_OUT = "sensor_out"
CONF_KA = "ka"
CONF_KB = "kb"

# Master mode
CONF_MASTER_MODE = "master_mode"
CONF_OPERATION = "operation_mode"
MODE_ON_OFF = "on_off"
MODE_CONTINUOUS = "continuous"
CONF_MIN_LOAD = "min_load"
CONF_SATELITES = "satelites"

CONTROL_START_DELAY = 0.2  # seconds, calculate pwm and offset before pwm loop
CONTROL_LEAD = 0.1  # seconds
SAT_CONTROL_LEAD = 0.15  # seconds
PWM_LAG = 0.05  # seconds

# MASTER_PWM_DELAY = 0.25  # seconds, calculate pwm and offset before pwm loop
NESTING_BALANCE = 0.1
# valve_control_mode
# CONF_VALVE_MODE = "PID_VALVE_mode"
CONF_GOAL = "goal"

SUPPORTED_PRESET_MODES = [
    PRESET_NONE,
    PRESET_AWAY,
]
