"""MultiZone thermostat.
Incl support for:
- multizone heating
- UKF filter on sensor
- various controllers:
    - temperature: PID
    - outdoor temperature: weather
    - valve position: PID
For more details about this platform, please refer to the README
"""

import asyncio
import logging
import datetime
from typing import Callable, Dict
import time

import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateEntity
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
from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, callback
from homeassistant.helpers import condition
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
    async_track_utc_time_change,
)

from homeassistant.helpers import entity_platform

# from homeassistant.helpers.update_coordinator.DataUpdateCoordinator import (
#     async_remove_listener,
# )
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from . import DOMAIN, PLATFORMS
from . import hvac_setting
from . import UKF_filter

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

# on_off mode
DEFAULT_HYSTERESIS_TOLERANCE = 0.5

# PWM/PID controller
DEFAULT_DIFFERENCE = 100
DEFAULT_MIN_DIFF = 0
DEFAULT_PWM = 0

DEFAULT_SENSOR_FILTER = 0

DEFAULT_AUTOTUNE = "none"
DEFAULT_AUTOTUNE_CONTROL_TYPE = "none"
DEFAULT_STEP_SIZE = "10"
DEFAULT_NOISEBAND = 0.5
DEFAULT_HEAT_METER = "none"


CONF_SENSOR = "sensor"
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_INITIAL_PRESET_MODE = "initial_preset_mode"

CONF_HVAC_MODE_MIN_TEMP = "min_temp"
CONF_HVAC_MODE_MAX_TEMP = "max_temp"
CONF_HVAC_MODE_INIT_TEMP = "initial_target_temp"
CONF_AWAY_TEMP = "away_temp"
CONF_PRECISION = "precision"
CONF_AREA = "room_area"
CONF_ENABLE_OLD_STATE = "restore_from_old_state"
CONF_ENABLE_OLD_PARAMETERS = "restore_parameters"
CONF_ENABLE_OLD_INTEGRAL = "restore_integral"
CONF_STALE_DURATION = "sensor_stale_duration"
CONF_PASSIVE_SWITCH_CHECK = "passive_switch_check"
CONF_PASSIVE_SWITCH_DURATION = "passive_switch_duration"

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

# proportional valve control (pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

# PID controller
CONF_PID_MODE = "PID_mode"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_D_AVG = "derative_avg"
CONF_SENSOR_FILTER = "sensor_filter"

CONF_AUTOTUNE = "autotune"
CONF_AUTOTUNE_CONTROL_TYPE = "autotune_control_type"
CONF_NOISEBAND = "noiseband"
CONF_AUTOTUNE_LOOKBACK = "autotune_lookback"
CONF_AUTOTUNE_STEP_SIZE = "tune_step_size"
# CONF_HEAT_METER = "heat_meter"
PRESET_PID_AUTOTUNE = "PID_autotune"
PRESET_VALVE_AUTOTUNE = "VALVE_autotune"

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

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE

SUPPORTED_HVAC_MODES = [HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_OFF]
SUPPORTED_PRESET_MODES = [
    PRESET_NONE,
    PRESET_AWAY,
    PRESET_PID_AUTOTUNE,
    PRESET_VALVE_AUTOTUNE,
]


def validate_initial_control_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate(obj: Dict) -> Dict:
        """Check this condition."""
        for hvac_mode in [HVAC_MODE_HEAT, HVAC_MODE_COOL]:
            if hvac_mode in obj:
                if all(
                    x in obj[hvac_mode]
                    for x in [CONF_ON_OFF_MODE, CONF_PROPORTIONAL_MODE]
                ):
                    raise vol.Invalid(
                        "The on_off and proportional mode have both been set {hvac_mode} mode"
                    )
            return obj

    return validate


def validate_initial_preset_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate_by_mode(obj: Dict, preset: str, config_preset: str):
        """Use a helper to validate mode by mode."""
        if HVAC_MODE_HEAT in obj.keys() and config_preset not in obj[HVAC_MODE_HEAT]:
            raise vol.Invalid(
                "The preset {preset} has been set as initial preset but the {config_preset} is not present on {HVAC_MODE_HEAT} mode"
            )
        if HVAC_MODE_COOL in obj.keys() and config_preset not in obj[HVAC_MODE_COOL]:
            raise vol.Invalid(
                "The preset {preset} has been set as initial preset but the {config_preset} is not present on {HVAC_MODE_COOL} mode"
            )

    def validate(obj: Dict) -> Dict:
        """Check this condition."""
        if CONF_INITIAL_PRESET_MODE in obj and obj[CONF_INITIAL_PRESET_MODE] != "none":
            if obj[CONF_INITIAL_PRESET_MODE] == PRESET_AWAY:
                validate_by_mode(obj, PRESET_AWAY, CONF_AWAY_TEMP)
        return obj

    return validate


def validate_initial_hvac_mode(*keys: str) -> Callable:
    """If an initial hvac mode has been set, check if this mode has been configured."""

    def validate(obj: Dict) -> Dict:
        """Check this condition."""
        if (
            CONF_INITIAL_HVAC_MODE in obj
            and obj[CONF_INITIAL_HVAC_MODE] != HVAC_MODE_OFF
            and obj[CONF_INITIAL_HVAC_MODE] not in obj.keys()
        ):
            raise vol.Invalid(
                "You cannot set an initial HVAC mode if you did not configure this mode {obj[CONF_INITIAL_HVAC_MODE]}"
            )
        return obj

    return validate


def check_presets_in_both_modes(*keys: str) -> Callable:
    """If one preset is set on one mode, then this preset is enabled and check it on the other modes."""

    def validate_by_preset(obj: Dict, conf: str):
        """Check this condition."""
        if conf in obj[HVAC_MODE_HEAT] and conf not in obj[HVAC_MODE_COOL]:
            raise vol.Invalid(
                "{preset} is set for {HVAC_MODE_HEAT} but not for {HVAC_MODE_COOL}"
            )
        if conf in obj[HVAC_MODE_COOL] and conf not in obj[HVAC_MODE_HEAT]:
            raise vol.Invalid(
                "{preset} is set for {HVAC_MODE_COOL} but not for {HVAC_MODE_HEAT}"
            )

    def validate(obj: Dict) -> Dict:
        if HVAC_MODE_HEAT in obj.keys() and HVAC_MODE_COOL in obj.keys():
            validate_by_preset(obj, CONF_AWAY_TEMP)
        return obj

    return validate


PID_autotune = {
    vol.Optional(CONF_AUTOTUNE, default=DEFAULT_AUTOTUNE): cv.string,
    vol.Optional(
        CONF_AUTOTUNE_CONTROL_TYPE,
        default=DEFAULT_AUTOTUNE_CONTROL_TYPE,
    ): cv.string,
    vol.Optional(CONF_AUTOTUNE_LOOKBACK): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    vol.Optional(
        CONF_AUTOTUNE_STEP_SIZE,
        default=DEFAULT_STEP_SIZE,
    ): vol.Coerce(float),
    vol.Optional(CONF_NOISEBAND, default=DEFAULT_NOISEBAND): vol.Coerce(float),
}

PID_control_options_opt = {
    vol.Optional(CONF_KP): vol.Coerce(float),
    vol.Optional(CONF_KI): vol.Coerce(float),
    vol.Optional(CONF_KD): vol.Coerce(float),
    vol.Optional(CONF_MIN_DIFFERENCE): vol.Coerce(float),
    vol.Optional(CONF_MAX_DIFFERENCE): vol.Coerce(float),
    **PID_autotune,
}

PID_control_options_req = {
    vol.Required(CONF_KP): vol.Coerce(float),
    vol.Required(CONF_KI): vol.Coerce(float),
    vol.Required(CONF_KD): vol.Coerce(float),
    vol.Optional(CONF_MIN_DIFFERENCE): vol.Coerce(float),
    vol.Optional(CONF_MAX_DIFFERENCE): vol.Coerce(float),
    **PID_autotune,
}

hvac_control_options = {
    vol.Required(CONF_ENTITY_ID): cv.entity_id,
    vol.Required(CONF_HVAC_MODE_MIN_TEMP, default=DEFAULT_MIN_TEMP_HEAT): vol.Coerce(
        float
    ),
    vol.Required(CONF_HVAC_MODE_MAX_TEMP, default=DEFAULT_MAX_TEMP_HEAT): vol.Coerce(
        float
    ),
    vol.Required(
        CONF_HVAC_MODE_INIT_TEMP, default=DEFAULT_TARGET_TEMP_HEAT
    ): vol.Coerce(float),
    vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
    vol.Optional(CONF_PASSIVE_SWITCH_DURATION): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
    # on_off
    vol.Optional(CONF_ON_OFF_MODE): vol.Schema(
        {
            vol.Optional(
                CONF_HYSTERESIS_TOLERANCE_ON,
                default=DEFAULT_HYSTERESIS_TOLERANCE,
            ): vol.Coerce(float),
            vol.Optional(
                CONF_HYSTERESIS_TOLERANCE_OFF,
                default=DEFAULT_HYSTERESIS_TOLERANCE,
            ): vol.Coerce(float),
            vol.Optional(CONF_MIN_CYCLE_DURATION): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
            vol.Optional(CONF_KEEP_ALIVE): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
        }
    ),
    # proportional mode"
    vol.Optional(CONF_PROPORTIONAL_MODE): vol.Schema(
        {
            vol.Required(CONF_CONTROL_REFRESH_INTERVAL): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
            vol.Optional(CONF_DIFFERENCE, default=DEFAULT_DIFFERENCE): vol.Coerce(
                float
            ),
            vol.Optional(CONF_MIN_DIFF, default=DEFAULT_MIN_DIFF): vol.Coerce(float),
            vol.Optional(CONF_PWM, default=DEFAULT_PWM): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
            vol.Optional(CONF_SENSOR_FILTER, default=DEFAULT_SENSOR_FILTER): vol.Coerce(
                int
            ),
            # PID mode
            vol.Optional(CONF_PID_MODE): vol.Schema(PID_control_options_req),
            # weather compensating mode"
            vol.Optional(CONF_WC_MODE): vol.Schema(
                {
                    vol.Required(CONF_KA): vol.Coerce(float),
                    vol.Required(CONF_KB): vol.Coerce(float),
                    vol.Optional(CONF_MAX_DIFFERENCE): vol.Coerce(float),
                }
            ),
            # master mode"
            vol.Optional(CONF_MASTER_MODE): vol.Schema(
                {
                    vol.Required(CONF_SATELITES): cv.ensure_list,
                    vol.Optional(CONF_GOAL): vol.Coerce(float),
                    **PID_control_options_opt,
                },
            ),
        }
    ),
}


PLATFORM_SCHEMA = vol.All(
    cv.has_at_least_one_key(HVAC_MODE_HEAT, HVAC_MODE_COOL),
    validate_initial_hvac_mode(),
    check_presets_in_both_modes(),
    validate_initial_preset_mode(),
    validate_initial_control_mode(),
    PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_NAME): cv.string,
            vol.Optional(CONF_SENSOR): cv.entity_id,
            vol.Optional(CONF_SENSOR_OUT): cv.entity_id,
            vol.Optional(
                CONF_INITIAL_HVAC_MODE, default=DEFAULT_INITIAL_HVAC_MODE
            ): vol.In(SUPPORTED_HVAC_MODES),
            vol.Optional(
                CONF_INITIAL_PRESET_MODE, default=DEFAULT_INITIAL_PRESET_MODE
            ): vol.In(SUPPORTED_PRESET_MODES),
            vol.Optional(CONF_PRECISION): vol.In(
                [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
            ),
            vol.Optional(CONF_AREA, default=DEFAULT_AREA): vol.Coerce(float),
            vol.Optional(CONF_UNIQUE_ID): cv.string,
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
            vol.Optional(HVAC_MODE_HEAT): vol.Schema(hvac_control_options),
            vol.Optional(HVAC_MODE_COOL): vol.Schema(hvac_control_options),
        }
    ),
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the multizone thermostat platform."""

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    platform = entity_platform.current_platform.get()
    assert platform

    platform.async_register_entity_service(  # type: ignore
        "min_diff",
        {
            vol.Required("hvac_mode"): cv.string,
            vol.Required("min_diff"): vol.Coerce(float),
        },
        "async_set_min_diff",
    )

    platform.async_register_entity_service(  # type: ignore
        "set_pid",
        {
            vol.Required("hvac_mode"): cv.string,
            vol.Required("control_mode"): cv.string,
            vol.Optional("kp"): vol.Coerce(float),
            vol.Optional("ki"): vol.Coerce(float),
            vol.Optional("kd"): vol.Coerce(float),
            vol.Optional("update", default=True): vol.Boolean,
        },
        "async_set_pid",
    )

    platform.async_register_entity_service(  # type: ignore
        "set_filter_mode",
        {
            vol.Required("hvac_mode"): cv.string,
            vol.Optional("mode"): vol.Coerce(float),
        },
        "async_set_filter_mode",
    )

    platform.async_register_entity_service(  # type: ignore
        "set_integral",
        {
            vol.Required("hvac_mode"): cv.string,
            vol.Required("control_mode"): cv.string,
            vol.Required("integral"): vol.Coerce(float),
        },
        "async_set_integral",
    )
    platform.async_register_entity_service(  # type: ignore
        "set_goal",
        {
            vol.Required("hvac_mode"): cv.string,
            vol.Required("goal"): vol.Coerce(float),
        },
        "async_set_goal",
    )
    platform.async_register_entity_service(  # type: ignore
        "set_ka_kb",
        {
            vol.Required("hvac_mode"): cv.string,
            vol.Optional("ka"): vol.Coerce(float),
            vol.Optional("kb"): vol.Coerce(float),
        },
        "async_set_ka_kb",
    )

    name = config.get(CONF_NAME)
    sensor_entity_id = config.get(CONF_SENSOR)
    sensor_out_entity_id = config.get(CONF_SENSOR_OUT)
    initial_hvac_mode = config.get(CONF_INITIAL_HVAC_MODE)
    precision = config.get(CONF_PRECISION)
    unit = hass.config.units.temperature_unit
    unique_id = config.get(CONF_UNIQUE_ID)
    initial_preset_mode = config.get(CONF_INITIAL_PRESET_MODE)
    area = config.get(CONF_AREA)
    sensor_stale_duration = config.get(CONF_STALE_DURATION)
    passive_switch = config.get(CONF_PASSIVE_SWITCH_CHECK)

    enable_old_state = config.get(CONF_ENABLE_OLD_STATE)
    enable_old_parameters = config.get(CONF_ENABLE_OLD_PARAMETERS)
    enable_old_integral = config.get(CONF_ENABLE_OLD_INTEGRAL)
    heat_conf = config.get(HVAC_MODE_HEAT)
    cool_conf = config.get(HVAC_MODE_COOL)

    hvac_def = {}
    enabled_hvac_modes = []

    # Append the enabled hvac modes to the list
    if heat_conf:
        enabled_hvac_modes.append(HVAC_MODE_HEAT)
        hvac_def[HVAC_MODE_HEAT] = heat_conf
        # hvac_def["heat"] = hvac_setting.HVAC_Setting(name, HVAC_MODE_HEAT, heat_conf)
    if cool_conf:
        enabled_hvac_modes.append(HVAC_MODE_COOL)
        hvac_def[HVAC_MODE_COOL] = cool_conf
        # hvac_def["cool"] = hvac_setting.HVAC_Setting(name, HVAC_MODE_COOL, cool_conf)

    async_add_entities(
        [
            MultiZoneThermostat(
                name,
                unit,
                unique_id,
                precision,
                area,
                sensor_entity_id,
                sensor_out_entity_id,
                hvac_def,
                enabled_hvac_modes,
                initial_hvac_mode,
                initial_preset_mode,
                enable_old_state,
                enable_old_parameters,
                enable_old_integral,
                sensor_stale_duration,
                passive_switch,
            )
        ]
    )


class MultiZoneThermostat(ClimateEntity, RestoreEntity):
    """Representation of a MultiZone Thermostat device."""

    def __init__(
        self,
        name,
        unit,
        unique_id,
        precision,
        area,
        sensor_entity_id,
        sensor_out_entity_id,
        hvac_def,
        enabled_hvac_modes,
        initial_hvac_mode,
        initial_preset_mode,
        enable_old_state,
        enable_old_parameters,
        enable_old_integral,
        sensor_stale_duration,
        passive_switch,
    ):
        """Initialize the thermostat."""
        self._name = name
        self._LOGGER = logging.getLogger().getChild(
            "multizone_thermostat." + self._name
        )
        self._LOGGER.info("initialise: %s", self._name)
        self._sensor_entity_id = sensor_entity_id
        self._sensor_out_entity_id = sensor_out_entity_id
        self._temp_precision = precision
        self._unit = unit
        self._hvac_def = {}
        for mode, mode_config in hvac_def.items():
            self._hvac_def[mode] = hvac_setting.HVAC_Setting(
                self._LOGGER.name, mode, mode_config
            )
        self._hvac_mode = initial_hvac_mode
        self._preset_mode = initial_preset_mode
        self._enabled_hvac_mode = enabled_hvac_modes
        self._enable_old_state = enable_old_state
        self._restore_parameters = enable_old_parameters
        self._restore_integral = enable_old_integral
        self._sensor_stale_duration = sensor_stale_duration
        self._passive_switch = passive_switch
        self._area = area
        self._emergency_stop = False
        self._current_temperature = None
        self._last_current_temperature = None
        self._outdoor_temperature = None
        self._old_mode = "off"
        self._hvac_on = None
        self._current_alive_time = None
        self._satelites = None
        self._kf_temp = None

        self._temp_lock = asyncio.Lock()
        if not self._sensor_entity_id:
            sensor_entity = "None"
        else:
            sensor_entity = self._sensor_entity_id
        if unique_id is not None:
            self._unique_id = unique_id
        else:
            if (
                HVAC_MODE_HEAT in enabled_hvac_modes
                and HVAC_MODE_COOL not in enabled_hvac_modes
            ):
                entity_id = self._hvac_def["heat"].get_hvac_switch
                self._unique_id = slugify(f"{DOMAIN}_{entity_id}_{sensor_entity}")
            elif (
                HVAC_MODE_HEAT not in enabled_hvac_modes
                and HVAC_MODE_COOL in enabled_hvac_modes
            ):
                entity_id = self._hvac_def["cool"].get_hvac_switch
                self._unique_id = slugify(f"{DOMAIN}_{entity_id}_{sensor_entity}")
            elif (
                HVAC_MODE_HEAT in enabled_hvac_modes
                and HVAC_MODE_COOL in enabled_hvac_modes
            ):
                entity_id_heat = self._hvac_def["heat"].get_hvac_switch
                entity_id_cool = self._hvac_def["cool"].get_hvac_switch
                self._unique_id = slugify(
                    f"{DOMAIN}_{entity_id_heat}_{entity_id_cool}_{sensor_entity}"
                )

    async def async_added_to_hass(self):
        """Run when entity about to be added.
        Attach the listeners.
        """
        self._LOGGER.info("init thermostat")
        await super().async_added_to_hass()

        # Add listeners to track changes from the sensor
        if self._sensor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._sensor_entity_id],
                    self._async_sensor_temperature_changed,
                )
            )

        if self._sensor_out_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._sensor_out_entity_id],
                    self._async_sensor_outdoor_temperature_changed,
                )
            )

        if (
            self._sensor_entity_id or self._sensor_out_entity_id
        ) and self._sensor_stale_duration:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_check_sensor_not_responding,
                    self._sensor_stale_duration,
                )
            )

        # Add listeners to track changes from the hvac switches
        entity_list = []
        for _, mode_def in self._hvac_def.items():
            entity_list.append(mode_def.get_hvac_switch)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                entity_list,
                self._async_switch_device_changed,
            )
        )

        # Add checker to track the hvac switches haven't changed for spec period
        if self._passive_switch:
            async_track_utc_time_change(
                self.hass,
                self._async_prevent_stuck_switch,
                hour=0,
                minute=0,
                second=0,
            )

        @callback
        def _async_startup(*_):
            """Init on startup."""
            if self._sensor_entity_id:
                sensor_state = self.hass.states.get(self._sensor_entity_id)
            else:
                sensor_state = None
            if sensor_state and sensor_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                self._async_update_current_temp(sensor_state.state)

            if self._sensor_out_entity_id:
                sensor_state = self.hass.states.get(self._sensor_out_entity_id)
            else:
                sensor_state = None
            if sensor_state and sensor_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                self._update_outdoor_temperature(sensor_state.state)

                self.async_write_ha_state()

        if self.hass.state == CoreState.running:
            _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        # Check if we have an old state, if so, restore it
        old_state = await self.async_get_last_state()

        if self._enable_old_state and old_state is not None:
            self._LOGGER.debug("Old state stored : %s", old_state)
            old_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
            old_hvac_mode = old_state.state
            old_temperature = old_state.attributes.get(ATTR_TEMPERATURE)
            self._LOGGER.debug(
                "Old state preset mode %s, hvac mode %s, temperature %s",
                old_preset_mode,
                old_hvac_mode,
                old_temperature,
            )

            if old_preset_mode is not None and old_preset_mode in self.preset_modes:
                self._preset_mode = old_preset_mode

            if old_hvac_mode is not None and old_hvac_mode in self.hvac_modes:

                self._LOGGER.debug("activate old hvac mode : %s", old_hvac_mode)

                if "hvac_def" in old_state.attributes:
                    try:
                        self._LOGGER.debug("restore old controller settings")
                        old_def = old_state.attributes["hvac_def"]
                        for key, data in old_def.items():
                            if key in list(self._hvac_def.keys()):
                                self._hvac_def[key].restore_reboot(
                                    data,
                                    self._restore_parameters,
                                    self._restore_integral,
                                )
                    except:
                        self._LOGGER.warning(
                            "error restoring old controller settings for: %s", key
                        )
                else:
                    self._LOGGER.warning("no old controller settings to restore")

                # init hvac mode
                await self.async_set_hvac_mode(old_hvac_mode, init=True)

                # Restore the target temperature
                if self._hvac_on:
                    min_temp, max_temp = self._hvac_on.get_target_temp_limits
                    if (
                        old_temperature is not None
                        and min_temp <= old_temperature <= max_temp
                    ):
                        self._hvac_on.target_temperature = old_temperature

            else:
                self._LOGGER.warning(
                    "%s is not valid mode. restoring default mode: %s",
                    old_hvac_mode,
                    self._hvac_mode,
                )
                await self.async_set_hvac_mode(self._hvac_mode, init=True)
        else:
            # init in case no restore is required
            if not self._hvac_mode:
                self._LOGGER.warning("no hvac mode specified: force off mode")
                self._hvac_mode = HVAC_MODE_OFF

            self._LOGGER.info("init default hvac mode: %s", self._hvac_mode)
            await self.async_set_hvac_mode(self._hvac_mode, init=True)

        # Ensure we update the current operation after changing the mode
        await self._async_operate()
        self.async_write_ha_state()

    @property
    def device_state_attributes(self):
        """attributes to include in entity"""
        tmp_dict = {}
        for key, data in self._hvac_def.items():
            tmp_dict[key] = data.get_variable_attr
        return {
            "current_temp_filt": self.current_temperature,
            "room_area": self._area,
            "hvac_def": tmp_dict,
        }



    async def async_set_min_diff(self, hvac_mode, min_diff):
        """Set new PID Controller min pwm value."""
        self._LOGGER.warning(
            "new minimum PID difference for %s to: %s", hvac_mode, min_diff
        )
        self._hvac_def[hvac_mode].min_diff(min_diff)
        self.async_write_ha_state()

    async def async_set_pid(
        self, hvac_mode, control_mode, kp=None, ki=None, kd=None, update=False
    ):
        """Set new PID Controller Kp,Ki,Kd value."""
        self._LOGGER.warning(
            "new PID for %s %s to: %s;%s;%s", hvac_mode, control_mode, kp, ki, kd
        )
        self._hvac_def[hvac_mode].set_pid_param(
            control_mode, kp=kp, ki=ki, kd=kd, update=update
        )
        self.async_write_ha_state()

    async def async_set_filter_mode(self, hvac_mode, mode=None):
        """Set new filter for the temp sensor."""

        if hvac_mode == HVAC_MODE_OFF:
            return

        if mode:
            self._hvac_def[hvac_mode].filter_mode = mode
            self._LOGGER.info(
                "modified sensor filter mode for %s to: %s", hvac_mode, mode
            )
        else:
            mode = self._hvac_on.filter_mode
            self._LOGGER.debug("new sensor filter mode for %s to: %s", hvac_mode, mode)

        if hvac_mode != self._hvac_mode:
            self._hvac_def[hvac_mode].current_state = None

        elif hvac_mode == self._hvac_mode:
            if mode == 0:
                self._kf_temp = None
                self._hvac_on.current_state = None
                self._hvac_on.current_temperature = self._current_temperature
            else:
                if not self._kf_temp:
                    if self._current_temperature:
                        self._kf_temp = UKF_filter.filterr(
                            self._current_temperature,
                            self._hvac_on.get_operate_cycle_time.seconds,
                            self._hvac_on.filter_mode,
                        )
                    else:
                        self._LOGGER.warning(
                            "new sensor filter mode (%s) but no temperature reading for %s",
                            mode,
                            hvac_mode,
                        )
                        return
                # update active filter
                else:
                    self._kf_temp.set_filter_mode(
                        self._hvac_on.filter_mode,
                        self._hvac_on.get_operate_cycle_time.seconds,
                    )
        self.async_write_ha_state()

    async def async_set_integral(self, hvac_mode, control_mode, integral):
        """Set new PID Controller integral value."""
        self._LOGGER.warning(
            "new PID integral for %s %s to: %s", hvac_mode, control_mode, integral
        )
        self._hvac_def[hvac_mode].integral(control_mode, integral)
        self.async_write_ha_state()

    async def async_set_goal(self, hvac_mode, goal):
        """Set new valve Controller goal value."""
        self._LOGGER.warning("new PID valve goal for %s to: %s", hvac_mode, goal)
        self._hvac_def[hvac_mode].goal(goal)
        self.async_write_ha_state()

    async def async_set_ka_kb(self, hvac_mode, ka=None, kb=None):
        """Set new weather Controller ka,kb value."""
        self._LOGGER.warning("new weatehr ka,kb %s to: %s;%s", hvac_mode, ka, kb)
        self._hvac_def[hvac_mode].set_ka_kb(ka=ka, kb=kb)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode, init=False):
        """Set hvac mode."""
        # No changes have been made
        if self._hvac_mode == hvac_mode:
            return
        if hvac_mode not in self.hvac_modes:
            self._LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return
        self._LOGGER.debug("HVAC mode changed to %s", hvac_mode)
        self._old_mode = self._hvac_mode
        self._hvac_mode = hvac_mode

        # stop autotune
        if self._hvac_on:
            if self._hvac_on.is_pid_autotune_active:
                self._hvac_on.start_pid("pid")
            elif self._hvac_on.is_valve_autotune_active:
                self._hvac_on.start_pid("valve")

            # restore preset mode
            self._preset_mode = PRESET_NONE
            # stop keep_live
            await self._async_update_keep_alive()
            # stop tracking satelites
            if self._hvac_on.is_master_mode:
                await self._async_track_satelites()

        # new hvac mode thus all switches off
        for key, _ in self._hvac_def.items():
            await self._async_switch_turn_off(hvac_def=key)

        if self._hvac_mode == HVAC_MODE_OFF:
            self._LOGGER.debug("HVAC mode is OFF. Turn the devices OFF and exit")
            self._hvac_on = None
            self.async_write_ha_state()
            return
        else:
            self._hvac_on = self._hvac_def[self._hvac_mode]
            await self.async_set_filter_mode(hvac_mode=self._hvac_mode)

            # reset time stamp pid to avoid integral run-off
            if self._hvac_on.is_hvac_proportional_mode:
                self.time_changed = time.time()

                if self._hvac_on.is_hvac_pid_mode or self._hvac_on.is_hvac_valve_mode:
                    self._hvac_on.pid_reset_time

                # start listening for outdoor sensors
                if self._hvac_on.is_hvac_wc_mode and self.outdoor_temperature:
                    self._hvac_on.outdoor_temperature = self.outdoor_temperature

            # start listener for satelite thermostats
            if self._hvac_on.is_master_mode:
                await self._async_track_satelites(
                    entity_list=self._hvac_on.get_satelites
                )
            else:
                await self._async_update_current_temp()
                # self._hvac_on.current_temperature = self.current_temperature

            # update listener
            await self._async_update_keep_alive(self._hvac_on.get_operate_cycle_time)

            if not init:
                await self._async_operate()

            # Ensure we update the current operation after changing the mode
            self.async_write_ha_state()

    async def _async_update_keep_alive(self, interval=None):
        """run main controller at specified interval"""
        self._LOGGER.debug("update 'keep alive' for %s", self._hvac_mode)
        if not interval:
            self._current_alive_time()
        else:
            self._current_alive_time = async_track_time_interval(
                self.hass, self._async_operate, interval
            )
            # self.async_on_remove(self._current_alive_time)

    async def _async_track_satelites(self, entity_list=None):
        """get changes from satelite thermostats"""
        # stop tracking
        if not entity_list:
            self._satelites()
            return
        else:
            satelites = ["climate." + sub for sub in entity_list]
            self._satelites = async_track_state_change_event(
                self.hass, satelites, self._async_satelite_thermostat_changed
            )

            for satelite in satelites:
                state = self.hass.states.get(satelite)
                if state:
                    self._send_satelite(state)

    def _send_satelite(self, state):
        """send satelite data to current hvac mode"""
        self._LOGGER.debug(
            "update from satelite: %s to state %s", state.name, state.state
        )
        if state.state in [
            STATE_OFF,
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ]:
            setpoint = None
            current_temp = None
            area = state.attributes.get("room_area")
            valve = None
        else:
            setpoint = state.attributes.get("temperature")
            current_temp = state.attributes.get("current_temperature")
            area = state.attributes.get("room_area")
            valve = state.attributes.get("hvac_def")[state.state]["valve_pos"]
        self._hvac_on.update_satelite(
            state.name, state.state, setpoint, current_temp, area, valve
        )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)

        if hvac_mode is None:
            hvac_mode = self._hvac_mode
        elif hvac_mode not in self.hvac_modes:
            self._LOGGER.warning(
                "Try to update temperature to %s for mode %s but this mode is not enabled",
                temperature,
                hvac_mode,
            )
            return

        if hvac_mode is None or hvac_mode == HVAC_MODE_OFF:
            self._LOGGER.warning("You cannot update temperature for OFF mode")
            return

        self._LOGGER.debug(
            "Temperature updated to %s for mode %s", temperature, hvac_mode
        )

        if (
            self.preset_mode == PRESET_AWAY
        ):  # when preset mode is away, change the temperature but do not operate
            self._LOGGER.debug(
                "Preset mode away when temperature is updated : skipping operate"
            )
            return

        self._hvac_on.target_temperature = temperature
        # self._target_temp = temperature

        if not self._hvac_mode == HVAC_MODE_OFF:
            await self._async_operate(force=True)

        self.async_write_ha_state()

    async def _async_sensor_temperature_changed(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        self._LOGGER.debug("Sensor temperature updated to %s", new_state.state)
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            await self._async_activate_emergency_stop()
            return

        await self._async_update_current_temp(new_state.state)

        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if self._hvac_mode != HVAC_MODE_OFF:
            if self._hvac_on.is_hvac_on_off_mode:
                await self._async_operate(sensor_changed=True)

        self.async_write_ha_state()

    async def _async_sensor_outdoor_temperature_changed(self, event):
        """Handle outdoor temperature changes."""
        new_state = event.data.get("new_state")
        self._LOGGER.debug("Sensor outdoor temperature updated to %s", new_state.state)
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            await self._async_activate_emergency_stop()
            return

        self._update_outdoor_temperature(new_state.state)

        # # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        # if not self._hvac_mode == HVAC_MODE_OFF:
        #     if not self._hvac_on.is_hvac_pwm_mode:
        #         await self._async_operate(sensor_changed=True)
        self.async_write_ha_state()

    async def _async_satelite_thermostat_changed(self, event):
        """Handle thermostat changes changes."""
        new_state = event.data.get("new_state")
        if not new_state:
            self._LOGGER.error("error receiving thermostat update. 'None' received")
            return
        self._LOGGER.debug(
            "receiving thermostat %s update. new state: %s",
            new_state.name,
            new_state.state,
        )
        self._send_satelite(new_state)

        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if not self._hvac_mode == HVAC_MODE_OFF:
            await self._async_operate(sensor_changed=True)
        self.async_write_ha_state()

    async def _async_check_sensor_not_responding(self, now=None):
        """Check if the sensor has emitted a value during the allowed stale period."""
        entity_list = []
        if self._sensor_entity_id:
            entity_list.append(self._sensor_entity_id)
        if self._sensor_out_entity_id:
            entity_list.append(self._sensor_out_entity_id)

        for entity_id in entity_list:
            sensor_state = self.hass.states.get(entity_id)
            if (
                datetime.datetime.now(datetime.timezone.utc) - sensor_state.last_updated
                > self._sensor_stale_duration
            ):
                self._LOGGER.debug(
                    "Time is %s, last changed is %s, stale duration is %s , limit is %s"
                    % (
                        datetime.datetime.now(datetime.timezone.utc),
                        sensor_state.last_updated,
                        datetime.datetime.now(datetime.timezone.utc)
                        - sensor_state.last_updated,
                        self._sensor_stale_duration,
                    )
                )
                self._LOGGER.warning(
                    "Sensor %s is stalled, call the emergency stop" % (entity_id)
                )
                await self._async_activate_emergency_stop()

            return

    async def _async_prevent_stuck_switch(self, now=None):
        """Check if the switch has not changed for a cetrain period andforce operation to avoid stuck or jammed."""
        entity_list = {}
        for hvac_def, mode_config in self._hvac_def.items():
            if mode_config.get_switch_stale:
                entity_list[hvac_def] = [
                    mode_config.get_hvac_switch,
                    mode_config.get_switch_stale,
                ]

        if not entity_list:
            self._LOGGER.warning(
                "jamming/stuck prevention activated but no duration set for switches"
            )
            return

        for hvac_def, data in entity_list.items():
            sensor_state = self.hass.states.get(data[0])

            if not sensor_state:
                self._LOGGER.waring(
                    "Stuck prevention no state (NoneType) for %s" % (data[0])
                )
                continue

            self._LOGGER.debug(
                "Switch %s stuck prevention check with last update %s"
                % (
                    data[0],
                    sensor_state.last_updated,
                )
            )

            if (
                datetime.datetime.now(datetime.timezone.utc) - sensor_state.last_updated
                > data[1]
            ):
                self._LOGGER.info(
                    "Switch %s stuck prevention activated: not changed state for %s"
                    % (
                        data[0],
                        datetime.datetime.now(datetime.timezone.utc)
                        - sensor_state.last_updated,
                    )
                )
                self.hass.async_create_task(
                    self._async_toggle_switch(hvac_def, data[0])
                )
                # self._async_toggle_switch(hvac_def, data[0])

    @callback
    async def _async_switch_device_changed(self, event):
        """Handle device switch state changes."""
        new_state = event.data.get("new_state")
        entity_id = event.data.get("entity_id")
        self._LOGGER.debug(
            "Switch of %s changed to %s",
            entity_id,
            new_state.state,
        )

        if new_state.state.lower() == "on":
            switch_on = True
        elif isinstance(new_state.state, str):
            switch_on = False
        elif new_state.state > 0:
            switch_on = True

        if not self._hvac_on and switch_on:
            # thermostat off thus all switches off

            for mode, data in self._hvac_def.items():

                if (
                    not data.stuck_loop
                    and data.get_hvac_switch == entity_id
                    and self._is_switch_active(hvac_def=mode)
                ):
                    self._LOGGER.warning(
                        "No swithces should be 'on' in 'off' mode: switch of %s changed has to %s. Force off",
                        entity_id,
                        new_state.state,
                    )
                    await self._async_switch_turn_off(hvac_def=mode, force=True)

        if self._hvac_on:
            if entity_id != self._hvac_on.get_hvac_switch:
                self._LOGGER.warning(
                    "Wrong switch of %s changed from %s",
                    entity_id,
                    new_state.state,
                )

        if new_state is None:
            return
        self.async_write_ha_state()

    async def _async_update_current_temp(self, current_temp=None):
        """Update thermostat, optionally with latest state from sensor."""
        try:
            self._emergency_stop = False
            if current_temp:
                self._LOGGER.debug("Current temperature updated to %s", current_temp)
                # store local in case current hvac mode is off
                if not self._kf_temp:
                    self._current_temperature = float(current_temp)
                else:
                    self._last_current_temperature = float(current_temp)

            if self._hvac_on:
                if not self._hvac_on.filter_mode:
                    self._hvac_on.current_temperature = self._current_temperature
                else:
                    if not self._kf_temp:
                        await self.async_set_filter_mode(hvac_mode=self._hvac_mode)
                    if self._kf_temp:
                        self._kf_temp.kf_predict()
                        if current_temp:
                            tmp_temperature = float(current_temp)
                        elif self._last_current_temperature:
                            tmp_temperature = self._last_current_temperature
                        else:
                            tmp_temperature = self._current_temperature

                        self._kf_temp.kf_update(tmp_temperature)

                        # store local in case current hvac mode is off
                        self._LOGGER.debug("kp update temp %s", self._kf_temp.get_temp)
                        self._current_temperature = self._kf_temp.get_temp

                        if self._hvac_on: 
                            if not self._hvac_on.is_master_mode:
                                self._hvac_on.current_state = [
                                    self._kf_temp.get_temp,
                                    self._kf_temp.get_vel,
                                ]

            self.async_write_ha_state()
        except ValueError as ex:
            self._LOGGER.error("Unable to update from sensor: %s", ex)

    def _update_outdoor_temperature(self, current_temp=None):
        """Update thermostat with latest state from outdoor sensor."""
        try:
            self._emergency_stop = False

            if current_temp:
                self._LOGGER.debug(
                    "Current outdoor temperature updated to %s", current_temp
                )
                self._outdoor_temperature = float(current_temp)
                if self._hvac_on:
                    self._hvac_on.outdoor_temperature = self._outdoor_temperature
        except ValueError as ex:
            self._LOGGER.error("Unable to update from sensor: %s", ex)

    async def _async_operate(self, time=None, sensor_changed=False, force=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            # time is passed by to the callback the async_track_time_interval function , and is set to "now"
            keepalive = time is not None  # boolean

            if self._emergency_stop:
                if keepalive:
                    self._LOGGER.debug(
                        "Keepalive in emergency stop = resend emergency stop"
                    )
                    await self._async_activate_emergency_stop()
                else:
                    self._LOGGER.warning("Cannot operate in emergency stop state")
                return

            if not self._hvac_on:
                return

            # update and check current temperatures
            if not sensor_changed and not self._hvac_on.is_master_mode:
                await self._async_update_current_temp()

            if self._hvac_on.current_temperature is None:
                self._LOGGER.warning("Current temp is None, cannot compare with target")
                return

            # when mode is on_off
            # on_off is also true when pwm = 0 therefore != _is_pwm_active
            if self._hvac_on.is_hvac_on_off_mode:
                # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit
                min_cycle_duration = self._hvac_on.get_min_on_off_cycle
                tolerance_on, tolerance_off = self._hvac_on.get_hysteris

                # if the call was made by a sensor change, check the min duration
                # in case of keep-alive (time not none) this test is ignored due to sensor_change = false
                if sensor_changed and min_cycle_duration is not None:

                    entity_id = self._hvac_on.get_hvac_switch
                    current_state = STATE_ON if self._is_switch_active() else STATE_OFF

                    long_enough = condition.state(
                        self.hass, entity_id, current_state, min_cycle_duration
                    )

                    if not long_enough:
                        self._LOGGER.debug(
                            "Operate - Min duration not expired, exiting (%s, %s, %s)",
                            min_cycle_duration,
                            current_state,
                            entity_id,
                        )
                        return
                target_temp = self._hvac_on.target_temperature
                target_temp_min = target_temp - tolerance_on
                target_temp_max = target_temp + tolerance_off
                current_temp = self._hvac_on.current_temperature

                self._LOGGER.debug(
                    "Operate - tg_min %s, tg_max %s, current %s, tg %s, ka %s",
                    target_temp_min,
                    target_temp_max,
                    current_temp,
                    target_temp,
                    keepalive,
                )

                # If keep-alive case, we force the order resend (this is the goal of keep alive)
                force_resend = keepalive

                if current_temp > target_temp_max:
                    await self._async_switch_turn_off(force=force_resend)
                elif current_temp <= target_temp_min:
                    await self._async_switch_turn_on(force=force_resend)

            # when mode is pwm
            else:
                """calculate control output and handle autotune"""
                self._LOGGER.debug("update controller")
                self._hvac_on.calculate(force)
                # restore preset mode when autotune is off
                if (
                    self._preset_mode == PRESET_PID_AUTOTUNE
                    and not self._hvac_on.is_pid_autotune_active
                ) or (
                    self._preset_mode == PRESET_VALVE_AUTOTUNE
                    and not self._hvac_on.is_valve_autotune_active
                ):
                    self._preset_mode = PRESET_NONE

                self.control_output = self._hvac_on.get_control_output
                self._LOGGER.debug(
                    "Obtained current control output: %s", self.control_output
                )
                await self._async_set_controlvalue()
        self.async_write_ha_state()

    async def _async_set_controlvalue(self):
        """convert control output to pwm signal"""
        force_resend = True
        pwm = self._hvac_on.get_pwm_mode
        difference = self._hvac_on.get_difference
        if pwm:
            if self.control_output == difference:
                if not self._is_switch_active():
                    await self._async_switch_turn_on(force=force_resend)

                self.time_changed = time.time()
            elif self.control_output > self._hvac_on.min_diff:
                await self._async_pwm_switch(
                    pwm * self.control_output / difference,
                    pwm * (difference - self.control_output) / difference,
                    time.time() - self.time_changed,
                )
            else:
                if self._is_switch_active():
                    await self._async_switch_turn_off(force=force_resend)
                    self.time_changed = time.time()
        else:
            if (
                self._hvac_on.min_diff > self.control_output
                and self._is_switch_active()
            ):
                await self._async_switch_turn_off(force=force_resend)
                self.time_changed = time.time()
            else:
                await self._async_switch_turn_on(force=force_resend)

    async def _async_pwm_switch(self, time_on, time_off, time_passed):
        """turn off and on the heater proportionally to controlvalue."""
        entity_id = self._hvac_on.get_hvac_switch

        if self._is_switch_active():
            if time_on < time_passed:
                self._LOGGER.debug(
                    "Time exceeds 'on-time' by %s sec: turn off: %s",
                    entity_id,
                    round(time_on - time_passed, 0),
                )

                await self._async_switch_turn_off()
                self.time_changed = time.time()
            else:
                self._LOGGER.debug(
                    "Time until %s turns off: %s sec", entity_id, time_on - time_passed
                )
        else:
            if time_off < time_passed:
                self._LOGGER.debug(
                    "Time finshed 'off-time' by %s sec: turn on: %s",
                    entity_id,
                    round(time_passed - time_off, 0),
                )

                await self._async_switch_turn_on()
                self.time_changed = time.time()
            else:
                self._LOGGER.debug(
                    "Time until %s turns on: %s sec", entity_id, time_off - time_passed
                )

    async def _async_switch_turn_on(self, hvac_def=None, control_val=None, force=False):
        """Turn switch toggleable device on."""
        self._LOGGER.debug("Turn ON")
        if hvac_def:
            _hvac_def = self._hvac_def[hvac_def]
        else:
            _hvac_def = self._hvac_on

        if _hvac_def:
            entity_id = _hvac_def.get_hvac_switch
        if _hvac_def.is_hvac_switch_on_off:
            if self._is_switch_active(hvac_def=hvac_def) and not force:
                self._LOGGER.debug("Switch already ON")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            self._LOGGER.debug("Order ON sent to switch device %s", entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
            )
        else:
            """valve mode"""
            if not control_val:
                control_val = self.control_output

            self._LOGGER.debug(
                "Change state of heater %s to %s",
                entity_id,
                control_val,
            )
            data = {
                ATTR_ENTITY_ID: entity_id,
                ATTR_VALUE: control_val,
            }
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_switch_turn_off(self, hvac_def=None, force=False):
        """Turn toggleable device off."""
        self._LOGGER.debug("Turn OFF called")
        if hvac_def:
            _hvac_def = self._hvac_def[hvac_def]
        else:
            _hvac_def = self._hvac_on
        if _hvac_def:
            entity_id = _hvac_def.get_hvac_switch

        if _hvac_def.is_hvac_switch_on_off:
            if not self._is_switch_active(hvac_def=hvac_def) and not force:
                self._LOGGER.debug("Switch already OFF")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            self._LOGGER.debug("Order OFF sent to switch device %s", entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
            )
        else:
            """valve mode"""
            self._LOGGER.debug(
                "Change state of switch %s to %s",
                entity_id,
                0,
            )
            data = {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 0}
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_toggle_switch(self, hvac_def, entity_id):
        """toggle the state of a switch temporarily and hereafter set it to 0 or 1"""
        self._hvac_def[hvac_def].stuck_loop = True

        if self._is_switch_active(hvac_def=hvac_def):
            self._LOGGER.info(
                "switch %s toggle state temporarily to OFF for 3min" % (entity_id)
            )
            await self._async_switch_turn_off(hvac_def=hvac_def, force=True)
            await asyncio.sleep(3 * 60)
            await self._async_switch_turn_on(
                hvac_def=hvac_def, control_val=100, force=True
            )
        else:
            self._LOGGER.info(
                "switch %s toggle state temporarily to ON for 3min" % (entity_id)
            )
            await self._async_switch_turn_on(
                hvac_def=hvac_def, control_val=100, force=True
            )
            await asyncio.sleep(3 * 60)
            await self._async_switch_turn_off(hvac_def=hvac_def, force=True)

        self._hvac_def[hvac_def].stuck_loop = False

    async def _async_activate_emergency_stop(self):
        """Send an emergency OFF order to HVAC switch."""
        self._LOGGER.debug("Emergency OFF order send")
        self._emergency_stop = True
        await self._async_switch_turn_off(force=True)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.
        This method must be run in the event loop and returns a coroutine.
        """
        if preset_mode not in self.preset_modes and preset_mode != PRESET_NONE:
            self._LOGGER.error(
                "This preset (%s) is not enabled (see the configuration)", preset_mode
            )
            return

        self._preset_mode = preset_mode
        self._hvac_on.preset_mode = preset_mode
        self._LOGGER.debug("Set preset mode to %s", preset_mode)

        await self._async_operate(force=True)
        self.async_write_ha_state()

    def _is_switch_active(self, hvac_def=None):
        """If the toggleable switch device is currently active."""
        if hvac_def:
            _hvac_def = self._hvac_def[hvac_def]
        else:
            _hvac_def = self._hvac_on
        entity_id = _hvac_def.get_hvac_switch

        if _hvac_def.is_hvac_switch_on_off:
            return self.hass.states.is_state(entity_id, STATE_ON)
        else:
            sensor_state = self.hass.states.get(entity_id)
            if not sensor_state:
                return False
            try:
                if float(sensor_state.state) > 0:
                    return True
                else:
                    return False
            except:
                self._LOGGER.error(
                    "on-off switch defined for proportional control (pwm=0)"
                )

    @property
    def supported_features(self):
        """Return the list of supported features."""
        if self.preset_modes == [PRESET_NONE]:
            return SUPPORT_TARGET_TEMPERATURE
        return SUPPORT_PRESET_MODE | SUPPORT_TARGET_TEMPERATURE

    @property
    def precision(self):
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        # Since this integration does not yet have a step size parameter
        # we have to re-use the precision as the step size for now.
        return self.precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        if not self._hvac_mode == HVAC_MODE_OFF:
            if self.preset_mode == PRESET_AWAY:
                return self._hvac_on.get_away_temp
            if self._hvac_on.is_master_mode:
                return self._hvac_on._master_setpoint
            if self._hvac_on.min_target_temp:
                return self._hvac_on.min_target_temp

        # Get default temp from super class
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if not self._hvac_mode == HVAC_MODE_OFF:
            if self.preset_mode == PRESET_AWAY:
                return self._hvac_on.get_away_temp
            if self._hvac_on.is_master_mode:
                return self._hvac_on._master_setpoint
            if self._hvac_on.max_target_temp:
                return self._hvac_on.max_target_temp

        # Get default temp from super class
        return super().max_temp

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of this thermostat."""
        return self._unique_id

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        if self._hvac_mode == HVAC_MODE_OFF:
            return self._current_temperature

        return (
            round(self._hvac_on.current_temperature, 3)
            if self._hvac_on.current_temperature
            else self._hvac_on.current_temperature
        )

    @property
    def outdoor_temperature(self):
        """Return the sensor outdoor temperature."""
        return self._outdoor_temperature

    @property
    def hvac_mode(self):
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.

        Need to be one of CURRENT_HVAC_*.
        """
        if self._hvac_mode == HVAC_MODE_OFF:
            return CURRENT_HVAC_OFF
        if self._hvac_mode == HVAC_MODE_COOL and self._is_switch_active():
            return CURRENT_HVAC_COOL
        if self._hvac_mode == HVAC_MODE_HEAT and self._is_switch_active():
            return CURRENT_HVAC_HEAT

        return CURRENT_HVAC_IDLE

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self._hvac_mode == HVAC_MODE_OFF:
            return None
        return self._hvac_on.target_temperature

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return self._enabled_hvac_mode + [HVAC_MODE_OFF]

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp."""
        return self._preset_mode

    @property
    def preset_modes(self):
        """Return a list of available preset modes."""
        modes = [PRESET_NONE]
        if self._hvac_on:
            if self._hvac_on.get_away_temp:
                modes = modes + [PRESET_AWAY]
            if self._hvac_on.is_autotune_present("pid"):
                modes = modes + [PRESET_PID_AUTOTUNE]
            if self._hvac_on.is_autotune_present("valve"):
                modes = modes + [PRESET_VALVE_AUTOTUNE]

        return modes
