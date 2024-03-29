"""Multizone constants."""
from datetime import timedelta
from enum import StrEnum

# general
DEFAULT_TARGET_TEMP_HEAT = 19.0
DEFAULT_TARGET_TEMP_COOL = 28.0
DEFAULT_MAX_TEMP_HEAT = 24
DEFAULT_MIN_TEMP_HEAT = 5
DEFAULT_MAX_TEMP_COOL = 40
DEFAULT_MIN_TEMP_COOL = 15
DEFAULT_DETAILED_OUTPUT = False
DEFAULT_SENSOR_FILTER = 0
DEFAULT_AREA = 0
DEFAULT_INCLUDE_VALVE_LAG = timedelta(seconds=0)

# on_off switch type
NC_SWITCH_MODE = "NC"
NO_SWITCH_MODE = "NO"

# MASTER
DEFAULT_MIN_LOAD = 0.15  # min heat load % room area
DEFAULT_MIN_VALVE_PWM = 0  # factor of master pwm

# safety routines
DEFAULT_PASSIVE_SWITCH = False
DEFAULT_PASSIVE_SWITCH_OPEN_TIME = timedelta(seconds=60)
DEFAULT_PASSIVE_CHECK_TIME = "02:00"

# restore old states
DEFAULT_OLD_STATE = False
DEFAULT_RESTORE_PARAMETERS = False
DEFAULT_RESTORE_INTEGRAL = False

# on_off mode
DEFAULT_HYSTERESIS_TOLERANCE = 0.5

# PWM/PID controller
DEFAULT_PWM_SCALE = 100
DEFAULT_MIN_DIFF = 0
DEFAULT_PWM = 0
DEFAULT_PWM_RESOLUTION = 50
DEFAULT_MASTER_SCALE_BOUND = 1

# MASTER
DEFAULT_OPERATION = "on_off"

# configuration variables
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_INITIAL_PRESET_MODE = "initial_preset_mode"
CONF_SWITCH_MODE = "switch_mode"
CONF_PASSIVE_SWITCH_CHECK = "passive_switch_check"
CONF_DETAILED_OUTPUT = "detailed_output"

CONF_SENSOR = "sensor"
CONF_FILTER_MODE = "filter_mode"

ATTR_HVAC_DEFINITION = "hvac_def"
ATTR_SELF_CONTROLLED = "self_controlled"
ATTR_SAT_ALLOWED = "satelite_allowed"
ATTR_CONTROL_MODE = "control_mode"
ATTR_CURRENT_TEMP_VEL = "current_temperature_velocity"
ATTR_CURRENT_OUTDOOR_TEMPERATURE = "current_outdoor_temp"
ATTR_FILTER_MODE = "filter_mode"
ATTR_DETAILED_OUTPUT = "detailed_output"
ATTR_EMERGENCY_MODE = "emergency mode"
ATTR_UPDATE_NEEDED = "update satelite"
ATTR_LAST_SWITCH_CHANGE = "switch_last_change"
ATTR_STUCK_LOOP = "stuck_loop"

PRESET_EMERGENCY = "emergency"
PRESET_RESTORE = "restore"

# only required for hvac_settings
CONF_TARGET_TEMP_INIT = "initial_target_temp"
CONF_TARGET_TEMP_MIN = "min_target_temp"
CONF_TARGET_TEMP_MAX = "max_target_temp"

CONF_PRECISION = "precision"
CONF_AREA = "room_area"
CONF_ENABLE_OLD_STATE = "restore_from_old_state"
CONF_ENABLE_OLD_PARAMETERS = "restore_parameters"
CONF_ENABLE_OLD_INTEGRAL = "restore_integral"
CONF_STALE_DURATION = "sensor_stale_duration"

CONF_EXTRA_PRESETS = "extra_presets"

CONF_PASSIVE_SWITCH_DURATION = "passive_switch_duration"
CONF_PASSIVE_SWITCH_OPEN_TIME = "passive_switch_opening_time"
CONF_PASSIVE_CHECK_TIME = "passive_switch_check_time"
CONF_INCLUDE_VALVE_LAG = "compensate_valve_lag"

ATTR_CONTROL_OUTPUT = "control_output"  # offset and pwm_output
ATTR_CONTROL_PWM_OUTPUT = "pwm_out"
ATTR_CONTROL_OFFSET = "offset"

# proportional valve control (pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

# controller config
CONF_CONTROL_REFRESH_INTERVAL = "control_interval"
CONF_PWM_DURATION = "pwm_duration"
CONF_PWM_SCALE = "pwm_scale"
CONF_PWM_SCALE_LOW = "pwm_scale_low"
CONF_PWM_SCALE_HIGH = "pwm_scale_high"
CONF_PWM_RESOLUTION = "pwm_resolution"
CONF_PWM_THRESHOLD = "pwm_threshold"
CONF_MASTER_SCALE_BOUND = "bounded_scale_to_master"


ATTR_PWM_THRESHOLD = "pwm_threshold"

# on_off thermostat
CONF_ON_OFF_MODE = "on_off_mode"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_HYSTERESIS_TOLERANCE_ON = "hysteresis_on"
CONF_HYSTERESIS_TOLERANCE_OFF = "hysteresis_off"

# proportional mode
CONF_PROPORTIONAL_MODE = "proportional_mode"

# PID controller
CONF_PID_MODE = "PID_mode"

PID_CONTROLLER = "PID_controller"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_WINDOW_OPEN_TEMPDROP = "window_open_tempdrop"

ATTR_KP = "kp"
ATTR_KI = "ki"
ATTR_KD = "kd"

# weather compensating mode
CONF_WC_MODE = "weather_mode"
CONF_SENSOR_OUT = "sensor_out"
CONF_KA = "ka"
CONF_KB = "kb"
ATTR_KA = "ka"
ATTR_KB = "kb"

# Master mode
CONF_MASTER_MODE = "master_mode"
CONF_MASTER_OPERATION_MODE = "operation_mode"
CONF_SATELITES = "satelites"
CONF_MIN_VALVE = "min_opening_for_propvalve"
CONF_CONTINUOUS_LOWER_LOAD = "lower_load_scale"

# nesting
ATTR_ROOMS = "rooms"
ATTR_SCALED_PWM = "scaled_pwm"
ATTR_ROUNDED_PWM = "rounded_pwm"


class NestingMode(StrEnum):
    """Modes for nesting."""

    MASTER_MIN_ON = "minimal_on"
    MASTER_BALANCED = "balanced"
    MASTER_CONTINUOUS = "continuous"


# control constants
CONTROL_START_DELAY = 1  #   # seconds, control loop start delay rel to time()
MASTER_CONTROL_LEAD = 1  # 0.1  # seconds, time between last sat and master control
SAT_CONTROL_LEAD = 0.5  # 0.15  # seconds, time between control loop sats
PWM_LAG = 0.5  # 0.05  # seconds
PWM_UPDATE_CHANGE = 0.05  # percentage, pwm difference above which an update is needed
CLOSE_TO_PWM = 0.1  # percentage, if time is close to next pwm loop
MIN_MASTER_LOAD = 0.25  # min load for nesting
NESTING_DOMINANCE = 0.75  # limit dominant room in nesting

NESTING_MARGIN = 1.1  # margin for continuous opening
START_MISALINGMENT = (
    30  # seconds , skip switch toggle when in near future switch operated
)

NESTING_MATRIX = 20
NESTING_BALANCE = 0.1


class OperationMode(StrEnum):
    """Operation modes for satelite thermostats."""

    PENDING = "pending"
    MASTER = "master"
    SELF = "self_controlled"
    NO_CHANGE = "no change"
