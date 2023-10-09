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
# 3 TODO: async_write_ha_state, async_schedule_update_ha_state, async_write_ha_state
from __future__ import annotations

import asyncio
import datetime
from datetime import timedelta
from collections.abc import Callable
import logging
import time
from typing import Any


import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    PLATFORM_SCHEMA,
    PRESET_AWAY,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
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
    STATE_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    STATE_OPEN,
    STATE_OPENING,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_JAMMED,
    STATE_PROBLEM,
)

from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, HomeAssistant, callback
from homeassistant.exceptions import ConditionError
from homeassistant.helpers import condition, entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (  # async_track_utc_time_change,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.template import state_attr
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
)

from . import DOMAIN, PLATFORMS, UKF_config, hvac_setting
from .const import *

SUPPORTED_HVAC_MODES = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
SUPPORTED_PRESET_MODES = [PRESET_NONE, PRESET_AWAY, PRESET_EMERGENCY]
SUPPORTED_MASTER_MODES = [MASTER_CONTINUOUS, MASTER_BALANCED, MASTER_MIN_ON]
ERROR_STATE = [STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_JAMMED, STATE_PROBLEM]
NOT_SUPPORTED_SWITCH_STATES = [STATE_OPEN, STATE_OPENING, STATE_CLOSED, STATE_CLOSING]


def validate_initial_control_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        for hvac_mode in [HVACMode.COOL, HVACMode.HEAT]:
            if hvac_mode in obj:
                if all(
                    x in obj[hvac_mode]
                    for x in [CONF_ON_OFF_MODE, CONF_PROPORTIONAL_MODE]
                ):
                    raise vol.Invalid(
                        "The on_off and proportional mode have both been set {} mode".format(
                            hvac_mode
                        )
                    )
        return obj

    return validate


def validate_window(*keys: str) -> Callable:
    """Check if filter is active when setting window open detection."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        for hvac_mode in [HVACMode.COOL, HVACMode.HEAT]:
            if hvac_mode in obj and CONF_FILTER_MODE not in obj:
                try:
                    if (
                        CONF_WINDOW_OPEN_TEMPDROP
                        in obj[hvac_mode][CONF_PROPORTIONAL_MODE][CONF_PID_MODE]
                    ):
                        raise vol.Invalid(
                            "window open check included for {} mode but required temperature filter not set".format(
                                hvac_mode
                            )
                        )
                except Exception:
                    pass

        return obj

    return validate


def validate_initial_sensors(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        for hvac_mode in [HVACMode.HEAT, HVACMode.COOL]:
            if hvac_mode in obj:
                if CONF_ON_OFF_MODE in obj[hvac_mode] and not CONF_SENSOR in obj:
                    raise vol.Invalid(
                        "on-off control defined but no temperature sensor for {} mode".format(
                            hvac_mode
                        )
                    )
                if CONF_PROPORTIONAL_MODE in obj[hvac_mode]:
                    if (
                        CONF_PID_MODE in obj[hvac_mode][CONF_PROPORTIONAL_MODE]
                        and not CONF_SENSOR in obj
                    ):
                        raise vol.Invalid(
                            "PID control defined but no temperature sensor for {} mode".format(
                                hvac_mode
                            )
                        )
                    if (
                        CONF_WC_MODE in obj[hvac_mode][CONF_PROPORTIONAL_MODE]
                        and not CONF_SENSOR_OUT in obj
                    ):
                        raise vol.Invalid(
                            "Weather control defined but no outdoor temperature sensor for {} mode".format(
                                hvac_mode
                            )
                        )
                if CONF_MASTER_MODE in obj[hvac_mode]:
                    if CONF_SATELITES not in obj[hvac_mode][CONF_MASTER_MODE]:
                        raise vol.Invalid(
                            "Master mode defined but no satelite thermostats for {} mode".format(
                                hvac_mode
                            )
                        )
                    pwm_duration = timedelta(
                        seconds=obj[hvac_mode][CONF_MASTER_MODE][CONF_PWM_DURATION].get(
                            "seconds", 0
                        ),
                        hours=obj[hvac_mode][CONF_MASTER_MODE][CONF_PWM_DURATION].get(
                            "hours", 0
                        ),
                    )
                    cntrl_duration = timedelta(
                        seconds=obj[hvac_mode][CONF_MASTER_MODE][
                            CONF_CONTROL_REFRESH_INTERVAL
                        ].get("seconds", 0),
                        hours=obj[hvac_mode][CONF_MASTER_MODE][
                            CONF_CONTROL_REFRESH_INTERVAL
                        ].get("hours", 0),
                    )
                    if pwm_duration.seconds > 0 and pwm_duration != cntrl_duration:
                        raise vol.Invalid(
                            "Master mode {} ({} sec) not equal {} ({} sec)".format(
                                str(CONF_PWM_DURATION),
                                pwm_duration.seconds,
                                str(CONF_CONTROL_REFRESH_INTERVAL),
                                cntrl_duration.seconds,
                            ),
                        )

        return obj

    return validate


def validate_initial_preset_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate_by_mode(obj: dict[str, Any], preset: str, config_preset: str):
        """Use a helper to validate mode by mode."""
        if HVACMode.HEAT in obj.keys() and config_preset not in obj[HVACMode.HEAT]:
            raise vol.Invalid(
                "The preset {} has been set as initial preset but the {} is not present on {} mode".format(
                    preset, config_preset, HVACMode.HEAT
                )
            )
        if HVACMode.COOL in obj.keys() and config_preset not in obj[HVACMode.COOL]:
            raise vol.Invalid(
                "The preset {} has been set as initial preset but the {} is not present on {} mode".format(
                    preset, config_preset, HVACMode.COOL
                )
            )

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        if CONF_INITIAL_PRESET_MODE in obj and obj[CONF_INITIAL_PRESET_MODE] != "none":
            if obj[CONF_INITIAL_PRESET_MODE] == PRESET_AWAY:
                validate_by_mode(obj, PRESET_AWAY, CONF_TARGET_TEMP_AWAY)
        return obj

    return validate


def validate_initial_hvac_mode(*keys: str) -> Callable:
    """If an initial hvac mode has been set, check if this mode has been configured."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        if (
            CONF_INITIAL_HVAC_MODE in obj
            and obj[CONF_INITIAL_HVAC_MODE] != HVACMode.OFF
            and obj[CONF_INITIAL_HVAC_MODE] not in obj.keys()
        ):
            raise vol.Invalid(
                "You cannot set an initial HVAC mode if you did not configure this mode {}".format(
                    obj[CONF_INITIAL_HVAC_MODE]
                )
            )
        return obj

    return validate


def check_presets_in_both_modes(*keys: str) -> Callable:
    """If one preset is set on one mode, then this preset is enabled and check it on the other modes."""

    def validate_by_preset(obj: dict[str, Any], conf: str):
        """Check this condition."""
        if conf in obj[HVACMode.HEAT] and conf not in obj[HVACMode.COOL]:
            raise vol.Invalid(
                "{} is set for {} but not for {}".format(
                    conf, HVACMode.HEAT, HVACMode.COOL
                )
            )
        if conf in obj[HVACMode.COOL] and conf not in obj[HVACMode.HEAT]:
            raise vol.Invalid(
                "{} is set for {} but not for {}".format(
                    conf, HVACMode.COOL, HVACMode.HEAT
                )
            )

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        if HVACMode.HEAT in obj.keys() and HVACMode.COOL in obj.keys():
            validate_by_preset(obj, CONF_TARGET_TEMP_AWAY)
        return obj

    return validate


# Configuration of thermostats
hvac_control_options = {
    vol.Required(CONF_ENTITY_ID): cv.entity_id,  # switch to control
    vol.Optional(CONF_SWITCH_MODE, default=NC_SWITCH_MODE): vol.In(
        [NC_SWITCH_MODE, NO_SWITCH_MODE]
    ),
    vol.Optional(CONF_PASSIVE_SWITCH_DURATION): vol.All(
        cv.time_period, cv.positive_timedelta
    ),
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
    vol.Optional(CONF_TARGET_TEMP_AWAY): vol.Coerce(float),
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
    vol.Optional(CONF_TARGET_TEMP_AWAY): vol.Coerce(float),
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
    check_presets_in_both_modes(),
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
            vol.Optional(CONF_INITIAL_PRESET_MODE, default=PRESET_NONE): vol.In(
                SUPPORTED_PRESET_MODES
            ),
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


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the multizone thermostat platform."""

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

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

    name = config.get(CONF_NAME)
    sensor_entity_id = config.get(CONF_SENSOR)
    filter_mode = config.get(CONF_FILTER_MODE)
    sensor_out_entity_id = config.get(CONF_SENSOR_OUT)
    initial_hvac_mode = config.get(CONF_INITIAL_HVAC_MODE)
    precision = config.get(CONF_PRECISION)
    unit = hass.config.units.temperature_unit
    unique_id = config.get(CONF_UNIQUE_ID)
    initial_preset_mode = config.get(CONF_INITIAL_PRESET_MODE)
    area = config.get(CONF_AREA)
    sensor_stale_duration = config.get(CONF_STALE_DURATION)
    passive_switch = config.get(CONF_PASSIVE_SWITCH_CHECK)
    detailed_output = config.get(CONF_DETAILED_OUTPUT)
    enable_old_state = config.get(CONF_ENABLE_OLD_STATE)
    enable_old_parameters = config.get(CONF_ENABLE_OLD_PARAMETERS)
    enable_old_integral = config.get(CONF_ENABLE_OLD_INTEGRAL)
    heat_conf = config.get(HVACMode.HEAT)
    cool_conf = config.get(HVACMode.COOL)

    hvac_def = {}
    enabled_hvac_modes = []

    # Append the enabled hvac modes to the list
    if heat_conf:
        enabled_hvac_modes.append(HVACMode.HEAT)
        hvac_def[HVACMode.HEAT] = heat_conf
    if cool_conf:
        enabled_hvac_modes.append(HVACMode.COOL)
        hvac_def[HVACMode.COOL] = cool_conf

    async_add_entities(
        [
            MultiZoneThermostat(
                name,
                unit,
                unique_id,
                precision,
                area,
                sensor_entity_id,
                filter_mode,
                sensor_out_entity_id,
                hvac_def,
                enabled_hvac_modes,
                initial_hvac_mode,
                initial_preset_mode,
                detailed_output,
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

    _attr_should_poll = False

    def __init__(
        self,
        name,
        unit,
        unique_id,
        precision,
        area,
        sensor_entity_id,
        filter_mode,
        sensor_out_entity_id,
        hvac_def,
        enabled_hvac_modes,
        initial_hvac_mode,
        initial_preset_mode,
        detailed_output,
        enable_old_state,
        enable_old_parameters,
        enable_old_integral,
        sensor_stale_duration,
        passive_switch,
    ) -> None:
        """Initialize the thermostat."""
        self._temp_lock = asyncio.Lock()

        self._sensor_entity_id = sensor_entity_id
        self._sensor_out_entity_id = sensor_out_entity_id
        self._filter_mode = filter_mode
        self._kf_temp = None
        self._temp_precision = precision
        self._attr_temperature_unit = unit

        self._hvac_mode = HVACMode.OFF
        self._hvac_mode_init = initial_hvac_mode
        self._old_preset = None
        self._preset_mode = initial_preset_mode
        self._enabled_hvac_mode = enabled_hvac_modes
        self._enable_old_state = enable_old_state
        self._restore_parameters = enable_old_parameters
        self._restore_integral = enable_old_integral
        self._sensor_stale_duration = sensor_stale_duration
        self._passive_switch = passive_switch
        self._area = area
        self._detailed_output = detailed_output
        self._emergency_stop = []
        self._current_temperature = None
        self._outdoor_temperature = None
        self._old_mode = "off"
        self._hvac_on = None
        self._loop_controller = None
        self._loop_pwm = None
        self._start_pwm = None
        self._stop_pwm = None
        self._loop_stuck_switch = None
        self._satelites = None
        self.time_changed = None
        self._pwm_start_time = None
        self._sat_id = 0
        self.control_output = {ATTR_CONTROL_OFFSET: 0, ATTR_CONTROL_PWM_OUTPUT: 0}
        self._self_controlled = OperationMode.SELF

        # check if it is master for Hvacmode.off
        self.is_master = False
        self._attr_name = name
        for _, hvac_mode in hvac_def.items():
            if CONF_MASTER_MODE in hvac_mode:
                self.is_master = True
                self._attr_name = OperationMode.MASTER

        # setup control modes
        self._hvac_def = {}
        for hvac_mode, mode_config in hvac_def.items():
            self._hvac_def[hvac_mode] = hvac_setting.HVACSetting(
                self._attr_name,
                hvac_mode,
                mode_config,
                self._area,
                self._detailed_output,
            )

        self._logger = logging.getLogger(DOMAIN).getChild(name)
        self._logger.info("initialise")

        if unique_id is not None:
            self._attr_unique_id = unique_id
        else:
            self._attr_unique_id = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added.
        Attach the listeners.
        """
        self._logger.info("init thermostat")
        await super().async_added_to_hass()

        # Add listeners to track changes from the temp sensor
        if self._sensor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._sensor_entity_id],
                    self._async_indoor_temp_change,
                )
            )
        # Add listeners to track changes from the outdoor temp sensor
        if self._sensor_out_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._sensor_out_entity_id],
                    self._async_outdoor_temp_change,
                )
            )

        # routine to check if state updates from sensor have stopped
        if (
            self._sensor_entity_id or self._sensor_out_entity_id
        ) and self._sensor_stale_duration:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_stale_sensor_check,
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
                self._async_switches_change,
            )
        )
        if self._passive_switch:
            # run at night
            async_track_time_change(
                self.hass,
                self._async_stuck_switch_check,
                hour=2,
                minute=0,
                second=0,
            )

        async def _async_startup(*_):
            """Init on startup."""
            save_state = False
            if self._sensor_entity_id:
                sensor_state = self.hass.states.get(self._sensor_entity_id)
            else:
                sensor_state = None
            if sensor_state and sensor_state.state not in ERROR_STATE:
                await self._async_update_current_temp(sensor_state.state)
                save_state = True

            if self._sensor_out_entity_id:
                sensor_state = self.hass.states.get(self._sensor_out_entity_id)
            else:
                sensor_state = None
            if sensor_state and sensor_state.state not in ERROR_STATE:
                self._async_update_outdoor_temperature(sensor_state.state)
                save_state = True

            if save_state:
                self.async_write_ha_state()

            # Check if we have an old state, if so, restore it
            if (old_state := await self.async_get_last_state()) is not None:
                if not self._enable_old_state:
                    # init in case no restore is required
                    if not self._hvac_mode_init:
                        self._logger.warning(
                            "no initial hvac mode specified: force off mode"
                        )
                        self._hvac_mode_init = HVACMode.OFF
                    self._logger.info(
                        "init default hvac mode: '%s'", self._hvac_mode_init
                    )
                else:
                    self.restore_old_state(old_state)

            await self.async_set_hvac_mode(self._hvac_mode_init)
            # self.async_write_ha_state() # set hvac mode has already write state

        if self.hass.state == CoreState.running:
            await _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

    def restore_old_state(self, old_state):
        """function to restore old state/config"""
        self._logger.debug("Old state stored : '%s'", old_state)

        try:
            if old_state is None:
                self._hvac_mode_init = HVACMode.OFF
                raise ValueError("No old state, init in default off mode")

            old_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
            if old_preset_mode is None:
                old_preset_mode = "none"
            old_hvac_mode = old_state.state
            old_temperature = old_state.attributes.get(ATTR_TEMPERATURE)
            self._logger.debug(
                "Old state preset mode %s, hvac mode %s, temperature '%s'",
                old_preset_mode,
                old_hvac_mode,
                old_temperature,
            )

            if (
                old_hvac_mode is None
                or old_preset_mode not in self.preset_modes
                or old_hvac_mode not in self.hvac_modes
                or ATTR_HVAC_DEFINITION not in old_state.attributes
            ):
                raise ValueError("Invalid old state, init in default off mode")

            self._logger.info("restore old controller settings")
            self._hvac_mode_init = old_hvac_mode
            self._preset_mode = old_preset_mode
            self._self_controlled = old_state.attributes.get(
                ATTR_SELF_CONTROLLED, OperationMode.SELF
            )
            if self._self_controlled == OperationMode.MASTER:
                self._self_controlled = OperationMode.PENDING

            if self._hvac_mode_init != HVACMode.OFF:
                old_def = old_state.attributes[ATTR_HVAC_DEFINITION]
                for key, data in old_def.items():
                    if key in self._hvac_def:
                        self._hvac_def[key].restore_reboot(
                            data,
                            self._restore_parameters,
                            self._restore_integral,
                        )

                # Restore the target temperature
                min_temp, max_temp = self._hvac_def[
                    old_hvac_mode
                ].get_target_temp_limits
                if (
                    old_temperature is not None
                    and min_temp <= old_temperature <= max_temp
                ):
                    self._hvac_def[old_hvac_mode].target_temperature = old_temperature

        except ValueError as eror:
            self._hvac_mode_init = HVACMode.OFF
            self._logger.warning("restoring old state failed:%s", str(eror))
            return

    @property
    def extra_state_attributes(self):
        """attributes to include in entity"""
        tmp_dict = {}
        for key, data in self._hvac_def.items():
            tmp_dict[key] = data.get_variable_attr
        if self.is_master:
            return {
                CONF_AREA: self._area,
                ATTR_HVAC_DEFINITION: tmp_dict,
                "Emergency_mode": self._emergency_stop,
            }
        else:
            return {
                "Emergency_mode": self._emergency_stop,
                ATTR_SELF_CONTROLLED: self._self_controlled,
                ATTR_CURRENT_OUTDOOR_TEMPERATURE: self.outdoor_temperature,
                ATTR_FILTER_MODE: self.filter_mode,
                CONF_AREA: self._area,
                ATTR_HVAC_DEFINITION: tmp_dict,
            }

    def set_detailed_output(self, hvac_mode: HVACMode, new_mode):
        """configure attribute output level"""
        self._hvac_def[hvac_mode].set_detailed_output(new_mode)
        self.schedule_update_ha_state()

    @callback
    def async_set_pwm_threshold(self, hvac_mode: HVACMode, new_threshold):
        """Set new PID Controller min pwm value."""
        self._logger.info(
            "new minimum for pwm scale for '%s' to: '%s'", hvac_mode, new_threshold
        )
        self._hvac_def[hvac_mode].pwm_threshold(new_threshold)
        self.schedule_update_ha_state()

    @callback
    def async_set_pid(
        self, hvac_mode: HVACMode, kp=None, ki=None, kd=None, update=False
    ):  # pylint: disable=invalid-name
        """Set new PID Controller Kp,Ki,Kd value."""
        self._logger.info("new PID for '%s' to: %s;%s;%s", hvac_mode, kp, ki, kd)
        self._hvac_def[hvac_mode].set_pid_param(kp=kp, ki=ki, kd=kd, update=update)
        self.schedule_update_ha_state()

    async def async_set_filter_mode(self, mode):
        """to change filter mode from HA"""
        # TODO
        # await self.hass.async_add_executor_job(self.set_filter_mode(mode))
        # self.hass.create_task(self.set_filter_mode(mode))
        self.set_filter_mode(mode)

    def set_filter_mode(self, mode):
        """Set new filter for the temp sensor."""
        self._filter_mode = mode
        self._logger.info("modified sensor filter mode to: '%s'", mode)

        if mode == 0:
            self._current_temperature = self.current_temperature
            self._kf_temp = None
            if self._hvac_on:
                self._hvac_on.current_state = None
                self._hvac_on.current_temperature = self.current_temperature
        else:
            cycle_time = 60  # dt is updated when calling predict

            if not self._kf_temp:
                if self._current_temperature is not None:
                    self._kf_temp = UKF_config.UKFFilter(
                        self._current_temperature,
                        cycle_time,
                        self.filter_mode,
                    )
                else:
                    self._logger.info(
                        "new sensor filter mode (%s) but no temperature reading",
                        mode,
                    )
                    return
            # update active filter
            else:
                self._kf_temp.set_filter_mode(
                    self.filter_mode,
                    cycle_time,
                )

    @callback
    def async_set_integral(self, hvac_mode: HVACMode, integral):
        """Set new PID Controller integral value."""
        self._logger.info("new PID integral for '%s' to: '%s'", hvac_mode, integral)
        self._hvac_def[hvac_mode].set_integral(integral)
        self.schedule_update_ha_state()

    @callback
    def async_set_goal(self, hvac_mode: HVACMode, goal):
        """Set new valve Controller goal value."""
        self._logger.info("new PID valve goal for '%s' to: '%s'", hvac_mode, goal)
        self._hvac_def[hvac_mode].goal(goal)
        self.schedule_update_ha_state()

    @callback
    def async_set_ka_kb(
        self, hvac_mode: HVACMode, ka=None, kb=None
    ):  # pylint: disable=invalid-name
        """Set new weather Controller ka,kb value."""
        self._logger.info("new weatehr ka,kb '%s' to: %s;%s", hvac_mode, ka, kb)
        self._hvac_def[hvac_mode].set_ka_kb(ka=ka, kb=kb)
        self.schedule_update_ha_state()

    @callback
    def async_set_satelite_mode(
        self, control_mode, offset=None, sat_id=0, pwm_start_time=0
    ):
        """
        to control satelite routines called from master
        control_mode 'no_change' to only update offset
        """
        self._logger.info(
            "sat update received for mode:'%s'; offset:'%s'", control_mode, offset
        )
        # no current and no previous thus return
        if self._old_mode == HVACMode.OFF and self._hvac_on is None:
            self._self_controlled = control_mode
            self._pwm_start_time = pwm_start_time
            return

        if self._hvac_on is not None:
            hvac_ref = self._hvac_on
        else:
            # if current mode is off , change old state
            hvac_ref = self._hvac_def[self._old_mode]

        # update offset
        if offset is not None:
            hvac_ref.time_offset = offset
            self.control_output[ATTR_CONTROL_OFFSET] = offset
        else:
            hvac_ref.time_offset = 0
            self.control_output[ATTR_CONTROL_OFFSET] = 0

        if control_mode == OperationMode.NO_CHANGE:
            # pass
            self._logger.debug("sat update: run controller")
            self.hass.create_task(self._async_controller_pwm())
        else:
            # only when valve is pwm mode not proportional
            if (
                hvac_ref.is_hvac_proportional_mode
            ):  # and hvac_ref.is_hvac_switch_on_off:
                if (
                    control_mode == OperationMode.SELF
                    and self._self_controlled != OperationMode.SELF
                ):
                    self._self_controlled = OperationMode.SELF
                    self._sat_id = 0

                elif control_mode == OperationMode.MASTER:
                    if self._self_controlled in [
                        OperationMode.PENDING,
                        OperationMode.SELF,
                    ]:
                        # new hvac mode thus all switches off
                        self._pwm_start_time = pwm_start_time
                        self._sat_id = sat_id
                        self._self_controlled = OperationMode.MASTER

                        if self._hvac_on is not None:
                            # stop keep_live
                            self._logger.debug(
                                "sat update: stopping controller routine"
                            )
                            if self._loop_controller:
                                self._async_routine_controller()
                            if self._loop_pwm:
                                self._logger.debug("sat update: stopping pwm routine")
                                self._async_routine_pwm()

                            # cancel scheduled switch routines
                            self._async_cancel_pwm_routines()

                            for key, _ in self._hvac_def.items():
                                self.hass.create_task(
                                    self._async_switch_turn_off(hvac_mode=key)
                                )

                            async_track_point_in_utc_time(
                                self.hass,
                                self.async_routine_controller_factory(
                                    self._hvac_on.get_operate_cycle_time
                                ),
                                datetime.datetime.fromtimestamp(
                                    self._pwm_start_time
                                    - sat_id * SAT_CONTROL_LEAD
                                    - MASTER_CONTROL_LEAD
                                ),
                            )

                            # run pwm just after controller
                            async_track_point_in_utc_time(
                                self.hass,
                                self.async_routine_pwm_factory(
                                    self._hvac_on.get_pwm_time
                                ),
                                datetime.datetime.fromtimestamp(
                                    self._pwm_start_time + PWM_LAG
                                ),
                            )
                            # await self._async_routine_controller(self._hvac_on.get_operate_cycle_time)

                else:
                    self._logger.warning(
                        "changing satelite opertion mode should not come here: c.mode=%s; self controlled=%s",
                        control_mode,
                        self._self_controlled,
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
            self._logger.warning(
                "Try to update temperature to '%s' for mode '%s' but this mode is not enabled",
                temperature,
                hvac_mode,
            )
            return

        if hvac_mode is None or hvac_mode == HVACMode.OFF:
            self._logger.warning("You cannot update temperature for OFF mode")
            return

        self._logger.debug(
            "Temperature updated to '%s' for mode '%s'", temperature, hvac_mode
        )

        if (
            self.preset_mode == PRESET_AWAY
        ):  # when preset mode is away, change the temperature but do not operate
            self._logger.debug(
                "Preset mode away when temperature is updated : skipping operate"
            )
            return

        self._hvac_on.target_temperature = round(temperature, 3)

        # operate in all cases except off
        if self._hvac_mode != HVACMode.OFF:
            await self._async_controller(force=True)

        # self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Main routine to set hvac mode."""
        # No changes have been made
        if self._hvac_mode == hvac_mode:
            return

        if hvac_mode not in self.hvac_modes:
            self._logger.error("Unrecognized hvac mode: '%s'", hvac_mode)
            return
        self._logger.info("HVAC mode changed to '%s'", hvac_mode)
        self._old_mode = self._hvac_mode
        self._hvac_mode = hvac_mode

        if self._hvac_on:
            # cancel scheduled switch routines
            self._async_cancel_pwm_routines()
            # stop keep_live
            self._async_routine_controller()
            if self._loop_pwm:
                self._async_routine_pwm()
            self.control_output = {ATTR_CONTROL_OFFSET: 0, ATTR_CONTROL_PWM_OUTPUT: 0}
            # stop tracking satelites
            if self.is_master:
                await self._async_routine_track_satelites()
                satelite_reset = {sat: 0 for sat in self._hvac_on.get_satelites}
                self._async_change_satelite_modes(
                    satelite_reset, control_mode=OperationMode.SELF
                )

        self._hvac_on = None
        # new hvac mode thus all switches off
        for key, _ in self._hvac_def.items():
            await self._async_switch_turn_off(hvac_mode=key)

        if self._hvac_mode == HVACMode.OFF:
            self._logger.info("HVAC mode is OFF. Turn the devices OFF and exit")
            self.async_write_ha_state()
            return

        self._hvac_on = self._hvac_def[self._hvac_mode]
        if self._preset_mode == PRESET_EMERGENCY:
            self._hvac_on.preset_mode = self._preset_mode

        # reset time stamp pid to avoid integral run-off
        if self._hvac_on.is_prop_pid_mode or self._hvac_on.is_valve_mode:
            self.time_changed = time.time()
            self._hvac_on.pid_reset_time()

        # start listening for outdoor sensors
        if self._hvac_on.is_wc_mode and self.outdoor_temperature is not None:
            self._hvac_on.outdoor_temperature = self.outdoor_temperature

        if (
            self._self_controlled == OperationMode.SELF
            or self._self_controlled == OperationMode.PENDING
        ):
            self._pwm_start_time = time.time() + CONTROL_START_DELAY

        if self._hvac_on.is_hvac_on_off_mode:
            # no need for pwm routine as controller assures update
            if self._hvac_on.get_operate_cycle_time:
                self._async_routine_controller(self._hvac_on.get_operate_cycle_time)

            async_track_point_in_utc_time(
                self.hass,
                self.async_run_controller_factory(force=True),
                datetime.datetime.fromtimestamp(self._pwm_start_time),
            )

        elif self.is_master or self._hvac_on.is_hvac_proportional_mode:
            # update and track satelites
            if self.is_master:
                # bring controllers of satelite in sync with master
                # use the pwm
                satelite_reset = {sat: 0 for sat in self._hvac_on.get_satelites}
                self._async_change_satelite_modes(
                    satelite_reset,
                    control_mode=OperationMode.MASTER,
                )

                # start tracking changes of satelites
                self.hass.async_create_task(
                    self._async_routine_track_satelites(
                        entity_list=self._hvac_on.get_satelites
                    )
                )

                # force first update of satelites
                for satelite in self._hvac_on.get_satelites:
                    state = self.hass.states.get("climate." + satelite)
                    if state:
                        self._hvac_on.update_satelite(state)

            # update pwm cycle
            if (
                self._self_controlled != OperationMode.SELF
                and self._hvac_on.get_pwm_time
            ):
                self.update_pwm_time()

            # run controller before pwm loop
            if self.is_master:
                lead_time = MASTER_CONTROL_LEAD
            else:
                lead_time = self._sat_id * SAT_CONTROL_LEAD - MASTER_CONTROL_LEAD
            async_track_point_in_utc_time(
                self.hass,
                self.async_routine_controller_factory(
                    self._hvac_on.get_operate_cycle_time
                ),
                datetime.datetime.fromtimestamp(self._pwm_start_time - lead_time),
            )
            await self._async_controller()

            # run pwm just after controller
            if self._hvac_on.get_pwm_time:
                async_track_point_in_utc_time(
                    self.hass,
                    self.async_routine_pwm_factory(self._hvac_on.get_pwm_time),
                    datetime.datetime.fromtimestamp(self._pwm_start_time + PWM_LAG),
                )

        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    @callback
    def async_routine_controller_factory(self, interval=None):
        """Generate turn on callbacks as factory."""

        async def async_run_routine(now):
            """run controller with interval."""
            self._async_routine_controller(interval=interval)

        return async_run_routine

    @callback
    def _async_routine_controller(self, interval=None):
        """run main controller at specified interval"""
        self._logger.debug("Update controller loop routine")
        if interval is None and self._loop_controller is not None:
            self._logger.debug("Cancel control loop")
            self._loop_controller()
            self._loop_controller = None
        elif interval is not None and self._loop_controller is not None:
            self._logger.debug("New loop, cancel current control loop")
            self._loop_controller()
            self._loop_controller = None
        elif interval is None:
            self._logger.warning("No control loop to stop")

        if interval and self._loop_controller is None:
            self._logger.debug("Define new control loop")
            self._loop_controller = async_track_time_interval(
                self.hass, self._async_controller, interval
            )
            self.async_on_remove(self._loop_controller)

    @callback
    def async_routine_pwm_factory(self, interval=None):
        """Generate pwm controller callbacks as factory."""

        async def async_run_routine(now):
            """run pwm controller."""
            self._async_routine_pwm(interval=interval)

        return async_run_routine

    @callback
    def _async_routine_pwm(self, interval=None):
        """run main pwm at specified interval"""
        self._logger.debug("Update pwm loop routine")
        if interval is None and self._loop_pwm is not None:
            self._logger.debug("Cancel pwm loop")
            self._loop_pwm()
            self._loop_pwm = None
        elif interval is not None and self._loop_pwm is not None:
            self._logger.debug("New loop, cancel current pwm loop")
            self._loop_pwm()
            self._loop_pwm = None
        elif interval is None:
            self._logger.warning("No pwm loop to stop")

        if interval and self._loop_pwm is None:
            self._logger.debug("Define new pwm update routine")
            if interval.seconds == 0:
                # no routine needed for proportional valve
                return

            self._loop_pwm = async_track_time_interval(
                self.hass, self._async_controller_pwm, interval
            )
            self.async_on_remove(self._loop_pwm)
            if self._self_controlled == OperationMode.SELF:
                self.hass.async_create_task(self._async_controller_pwm(force=True))

    async def _async_routine_track_satelites(self, entity_list=None):
        """get changes from satelite thermostats"""
        # stop tracking
        if not entity_list and self._satelites is not None:
            self._satelites()
            self._satelites = None

        elif entity_list and self._satelites is None:
            satelites = ["climate." + sub for sub in entity_list]
            self._satelites = async_track_state_change_event(
                self.hass, satelites, self._async_satelite_change
            )
            self.async_on_remove(self._satelites)

    @callback
    def _async_indoor_temp_change(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        self._logger.debug("Sensor temperature updated to '%s'", new_state.state)
        wrong_state = False
        if new_state is None or new_state.state in ERROR_STATE:
            self._logger.warning(
                "Sensor temperature {} invalid: {}, skip current state".format(
                    new_state.name, new_state.state
                )
            )
            wrong_state = True

        elif not is_float(new_state.state):
            self._logger.warning(
                "Sensor temperature {} unclear: {} type {}, skip current state".format(
                    new_state.name, new_state.state, type(new_state.state)
                )
            )
            wrong_state = True

        elif float(new_state.state) < -50 or float(new_state.state) > 50:
            self._logger.warning(
                "Sensor temperature {} unrealistic: {}, skip current state".format(
                    new_state.name, new_state.state
                )
            )
            wrong_state = True

        if wrong_state:
            # self._async_activate_emergency_stop("indoor temp update", sensor=self._sensor_entity_id)
            return

        self.hass.create_task(self._async_update_current_temp(new_state.state))

        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if (
            self._hvac_mode != HVACMode.OFF
            and self._hvac_mode is not None
            and self._hvac_on is not None
        ):
            if self._hvac_on.is_hvac_on_off_mode:
                self.hass.create_task(self._async_controller())

        # self.async_write_ha_state()

    @callback
    def _async_outdoor_temp_change(self, event):
        """Handle outdoor temperature changes."""
        new_state = event.data.get("new_state")
        self._logger.debug(
            "Sensor outdoor temperature updated to '%s'", new_state.state
        )
        if new_state is None or new_state.state in ERROR_STATE:
            self._logger.warning(
                "Sensor temperature {} invalid {}, skip current state".format(
                    new_state.name, new_state.state
                )
            )
            # self._async_activate_emergency_stop("outdoor temp update", sensor=self._sensor_out_entity_id)
            return

        self._async_update_outdoor_temperature(new_state.state)

        # when weather mode is active: do not call operate but let pwm cycle handle it

    @callback
    def _async_stale_sensor_check(self, now=None):
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
                self._logger.debug(
                    "'%s' last received update is %s, duration is '%s', limit is '%s'"
                    % (
                        entity_id,
                        sensor_state.last_updated,
                        datetime.datetime.now(datetime.timezone.utc)
                        - sensor_state.last_updated,
                        self._sensor_stale_duration,
                    )
                )
                self._logger.warning(
                    "Sensor '%s' has stalled, call the emergency stop" % (entity_id)
                )
                self._async_activate_emergency_stop("stale sensor", sensor=entity_id)

            return

    @callback
    def _async_stuck_switch_check(self, now):
        """Check if the switch has not changed for a certain period and force operation to avoid stuck or jammed."""

        if self._self_controlled == OperationMode.PENDING:
            # changing opretation mode, wait for the net loop
            return
        # operated by master and check if currently active
        elif self._self_controlled != OperationMode.SELF:
            master_mode = state_attr(
                self.hass, "climate." + self._self_controlled, "hvac_action"
            )

            if master_mode in [HVACMode.COOL, HVACMode.HEAT]:
                return

        entity_list = {}
        for hvac_mode, mode_config in self._hvac_def.items():
            if mode_config.get_switch_stale:
                entity_list[hvac_mode] = [
                    mode_config.get_hvac_switch,
                    mode_config.get_switch_stale,
                ]

        if not entity_list:
            self._logger.warning(
                "jamming/stuck prevention activated but no duration set for switches"
            )
            return

        for hvac_mode, data in entity_list.items():
            sensor_state = self.hass.states.get(data[0])

            if not sensor_state:
                self._logger.warning(
                    "Stuck prevention ignored %s, no state (NoneType)" % (data[0])
                )
                continue

            self._logger.debug(
                "Switch '%s' stuck prevention check with last update '%s'"
                % (
                    data[0],
                    sensor_state.last_updated,
                )
            )

            if (
                datetime.datetime.now(datetime.timezone.utc) - sensor_state.last_updated
                > data[1]
            ):
                self._logger.warning(
                    "Switch '%s' stuck prevention activated: not changed state for '%s'"
                    % (
                        data[0],
                        datetime.datetime.now(datetime.timezone.utc)
                        - sensor_state.last_updated,
                    )
                )
                self.hass.create_task(self._async_toggle_switch(hvac_mode, data[0]))

    @callback
    def _async_satelite_change(self, event):
        """Handle satelite thermostat changes."""
        new_state = event.data.get("new_state")
        if not new_state:
            self._logger.error("Error receiving thermostat update. 'None' received")
            return
        self._logger.debug(
            "Receiving update from '%s'",
            new_state.name,
        )
        if self._hvac_mode != new_state.state and self._hvac_mode is not None:
            self._logger.debug(
                "Update from satelite: '%s' state '%s' not matching to master state '%s', satelite removed",
                new_state.name,
                new_state.state,
                self._hvac_mode,
            )

        # check if satelite operating in correct mode
        if new_state.state == self.hvac_mode and new_state.attributes.get(
            ATTR_SELF_CONTROLLED
        ) in [True, OperationMode.PENDING]:
            self._async_change_satelite_modes(
                {new_state.name: 0},
                control_mode=OperationMode.MASTER,
            )

        # updating master controller and check if pwm needs update
        update_required = self._hvac_on.update_satelite(new_state)
        if update_required:
            self.hass.create_task(self._async_controller(force=True))

        # if master mode is active: do not call operate but let pwm cycle handle it
        self.schedule_update_ha_state(force_refresh=False)
        # self.async_write_ha_state()

    @callback
    def _async_switches_change(self, event):
        """Handle device switch state changes."""
        new_state = event.data.get("new_state")
        entity_id = event.data.get(ATTR_ENTITY_ID)
        self._logger.debug(
            "Switch off '%s' changed to '%s'",
            entity_id,
            new_state.state,
        )
        # catch multipe options
        switch_activated = False
        if new_state.state in ERROR_STATE:
            # self.hass.create_task(
            self._async_activate_emergency_stop("switch state change", sensor=entity_id)
        elif new_state.state in NOT_SUPPORTED_SWITCH_STATES:
            # MOD
            self._async_activate_emergency_stop(
                "not supported switch state {}".format(new_state.state),
                sensor=entity_id,
            )
        else:
            if entity_id in self._emergency_stop:
                self._emergency_stop.remove(entity_id)
                if not self._emergency_stop:
                    # MOD
                    self.hass.create_task(self.async_set_preset_mode(PRESET_RESTORE))

            if self._hvac_on is None:
                if self._old_mode != HVACMode.OFF:
                    _hvac_on = self._hvac_def[self._old_mode]
                elif self._old_mode == HVACMode.OFF:
                    _hvac_on = None
            else:
                _hvac_on = self._hvac_on

            if _hvac_on:
                if is_float(new_state.state):
                    if float(new_state.state) != 0:
                        switch_activated = True
                elif new_state.state == STATE_ON:
                    switch_activated = True
            else:
                # neither current or previous state thus make sure all are off
                switch_activated = True

            if self._hvac_on is None or _hvac_on is None:
                # not current active thermostat thus switch state change should not be triggered
                # unless stuck loop prevention is running
                for hvac_mode, data in self._hvac_def.items():
                    if (
                        not data.stuck_loop
                        and data.get_hvac_switch == entity_id
                        # and self._is_valve_open(hvac_mode=hvac_mode)
                    ):
                        self._logger.warning(
                            "No switches should be activated in hvac 'off' mode: restore switch '%s' from  %s to 'idle' state",
                            entity_id,
                            new_state.state,
                        )
                        self.hass.create_task(
                            self._async_switch_idle(hvac_mode=hvac_mode)
                        )

            elif self._hvac_on:
                if entity_id != self._hvac_on.get_hvac_switch and self._is_valve_open():
                    self._logger.warning(
                        "valve of %s is open. Other hvac mode switch changed '%s' changed to %s, keep in idle state",
                        self._hvac_mode,
                        entity_id,
                        new_state.state,
                    )
                    for hvac_mode, data in self._hvac_def.items():
                        if data.get_hvac_switch == entity_id:
                            self.hass.create_task(
                                self._async_switch_idle(hvac_mode=hvac_mode)
                            )
                            break

        # if new_state is None:
        #     return
        self.schedule_update_ha_state(force_refresh=False)
        # self.async_write_ha_state()  # this catches al switch changes

    async def _async_update_current_temp(self, current_temp=None):
        """Update thermostat, optionally with latest state from sensor."""
        if self._sensor_entity_id in self._emergency_stop:
            self._logger.info(
                "Recover from emergency mode, new temperature updated to '%s'",
                current_temp,
            )
            self._emergency_stop.remove(self._sensor_entity_id)
            if not self._emergency_stop:
                self.hass.async_create_task(self.async_set_preset_mode(PRESET_RESTORE))

        if current_temp:
            self._logger.debug("Current temperature updated to '%s'", current_temp)
            # store local in case current hvac mode is off
            try:
                self._current_temperature = float(current_temp)
            except ValueError:
                self._logger.info(
                    "No value reading from sensor reading '%s'",
                    current_temp,
                )
                return

            # setup filter after first temp reading
            if not self._kf_temp and self.filter_mode > 0:
                self.set_filter_mode(self.filter_mode)

        try:
            if self._kf_temp:
                self._kf_temp.kf_predict()
                if current_temp:
                    tmp_temperature = float(current_temp)
                elif self._current_temperature is not None:
                    tmp_temperature = self._current_temperature
                else:
                    tmp_temperature = None

                if tmp_temperature:
                    self._kf_temp.kf_update(tmp_temperature)

                self._logger.debug(
                    "filtered sensor update temp '%.2f'", self._kf_temp.get_temp
                )
            if current_temp:
                self.async_write_ha_state()  # called from controller thus not needed here
        except ValueError as ex:
            self._logger.error("Unable to update from sensor: '%s'", ex)
            self._async_activate_emergency_stop(
                "indoor temp update", sensor=self._sensor_entity_id
            )

    @callback
    def _async_update_outdoor_temperature(self, current_temp=None):
        """Update thermostat with latest state from outdoor sensor."""
        if self._sensor_out_entity_id in self._emergency_stop:
            self._logger.info(
                "Recover from emergency mode, new outdoor temperature updated to '%s'",
                current_temp,
            )
            self._emergency_stop.remove(self._sensor_out_entity_id)
            if not self._emergency_stop:
                # MOD
                self.hass.create_task(self.async_set_preset_mode(PRESET_RESTORE))
        try:
            if current_temp:
                self._logger.debug(
                    "Current outdoor temperature updated to '%s'", current_temp
                )
                self._outdoor_temperature = float(current_temp)
                if self._hvac_on:
                    self._hvac_on.outdoor_temperature = self._outdoor_temperature
        except ValueError as ex:
            self._logger.error("Unable to update from sensor: '%s'", ex)
            self._async_activate_emergency_stop(
                "outdoor temp update", sensor=self._sensor_out_entity_id
            )

    async def _async_update_controller_temp(self):
        """Update temperature to controller routines."""
        if self._hvac_on:
            if not self._kf_temp:
                self._hvac_on.current_temperature = self._current_temperature
            else:
                if not self.is_master:
                    self._hvac_on.current_state = [
                        self._kf_temp.get_temp,
                        self._kf_temp.get_vel,
                    ]

    @callback
    def _async_change_satelite_modes(self, data, control_mode=OperationMode.NO_CHANGE):
        """create tasks by master to update all satelites and/or update pwm offset"""

        if data:
            for satelite, offset in data.items():
                # factory as in device_sun_light_trigger
                # +1 to account for master
                if control_mode == OperationMode.MASTER:
                    sat_id = self._hvac_on.get_satelites.index(satelite) + 1
                else:
                    sat_id = 0
                self.hass.async_create_task(
                    self._async_send_satelite_data(
                        satelite,
                        offset,
                        control_mode=control_mode,
                        sat_id=sat_id,
                        pwm_start_time=self._pwm_start_time,
                    )
                )

        else:
            self._logger.debug("No satelite data to send")

    async def _async_send_satelite_data(
        self,
        satelite,
        offset,
        control_mode=OperationMode.NO_CHANGE,
        sat_id=0,
        pwm_start_time=0,
    ):
        """actual sending of control update to a satelite"""
        self._logger.debug(
            "send data to satelite %s %s %s", satelite, offset, control_mode
        )
        await self.hass.services.async_call(
            "multizone_thermostat",
            "satelite_mode",
            {
                ATTR_ENTITY_ID: "climate." + satelite,
                ATTR_CONTROL_MODE: control_mode,
                ATTR_CONTROL_OFFSET: offset,
                "sat_id": sat_id,
                "pwm_start_time": pwm_start_time,
            },
            context=self._context,
            # blocking=False,
        )

    async def _async_check_duration(self, routine, force):
        # when mode is on_off
        # on_off is also true when pwm = 0 therefore != _is_pwm_active

        # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit
        min_cycle_duration = self._hvac_on.get_min_on_off_cycle

        # if the call was made by a sensor change, check the min duration
        # in case of keep-alive (time not none) this test is ignored due to sensor_change = false
        if not force and not routine and min_cycle_duration.seconds != 0:
            entity_id = self._hvac_on.get_hvac_switch
            state = self.hass.states.get(entity_id).state
            try:
                long_enough = condition.state(
                    self.hass, entity_id, state, min_cycle_duration
                )
            except ConditionError:
                long_enough = False

            if not long_enough:
                self._logger.debug(
                    "Return from %s temp  update. Min duration (%s min) for state '%s' not expired",
                    entity_id,
                    min_cycle_duration.seconds / 60,
                    state,
                )
                return False

            else:
                return True
        else:
            return True

    def update_pwm_time(self):
        """determine if new pwm cycle has started and update cycle time"""
        pwm_duration = self._hvac_on.get_pwm_time.seconds
        if time.time() > self._pwm_start_time + pwm_duration:
            while time.time() > self._pwm_start_time + pwm_duration:
                self._pwm_start_time += pwm_duration

    @callback
    def async_run_controller_factory(self, force=False):
        """Generate controller callbacks as factory."""

        async def async_run_controller(now):
            """Run controller."""
            await self._async_controller(force=force)

        return async_run_controller

    async def _async_controller(self, now=None, force=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            # now is passed by to the callback the async_track_time_interval function , and is set to "now"
            routine = now is not None  # boolean

            if self._emergency_stop:
                self._logger.warning(
                    "Controller update: Cannot operate in emergency stop state, exit routine"
                )
                return

            if not self._hvac_on:
                self._logger.error(
                    "Control update should not be activate in preset off-mode, exit routine"
                )
                return

            # update and check current temperatures for pwm cycle
            if routine and not self.is_master:
                await self._async_update_current_temp()
            # send temperature to controller
            if not self.is_master:
                await self._async_update_controller_temp()

            if (
                self._hvac_on.is_hvac_on_off_mode
                or self._hvac_on.is_hvac_proportional_mode
            ):
                if self._sensor_entity_id and self._hvac_on.current_temperature is None:
                    self._logger.warning(
                        "Current temp is None while running controller routine."
                    )
                    self._async_activate_emergency_stop(
                        "controller", sensor=self._sensor_entity_id
                    )
                    return

            if self._hvac_on.is_wc_mode:
                if self._sensor_out_entity_id and (
                    self._hvac_on.outdoor_temperature is None
                    or self._hvac_on.target_temperature is None
                ):
                    self._logger.warning(
                        "Current outdoor temp is '%s' and setpoint is '%s' cannot run weather mode",
                        self._hvac_on.outdoor_temperature,
                        self._hvac_on.target_temperature,
                    )
                    self._async_activate_emergency_stop(
                        "controller", sensor=self._sensor_out_entity_id
                    )
                    return

            # for mode on_off
            if self._hvac_on.is_hvac_on_off_mode:
                if not await self._async_check_duration(routine, force):
                    return

            self._logger.debug(
                "Controller: calculate output, routine=%s; forced=%s", routine, force
            )
            if self._hvac_on.get_pwm_time.seconds:
                offset = (
                    time.time() - self._pwm_start_time
                ) / self._hvac_on.get_pwm_time.seconds
            else:
                offset = 0
            self._hvac_on.calculate(routine=routine, force=force, current_offset=offset)

            if self.is_master:
                # set offsets at satelites
                satelite_info = self._hvac_on.get_satelite_offset()
                self._async_change_satelite_modes(satelite_info)

            self.control_output = self._hvac_on.get_control_output
            self._logger.debug(
                "Obtained current control output: '%s'", self.control_output
            )

            if (
                force
                or self._hvac_on.is_hvac_on_off_mode
                or (
                    (self._hvac_on.is_hvac_proportional_mode or self.is_master)
                    and not self._hvac_on.get_pwm_time
                )
            ):
                self._logger.debug(
                    "Running pwm controller from control loop with 'force=%s'", force
                )
                # self.hass.async_create_task(self._async_controller_pwm(force=force))
                await self._async_controller_pwm(force=force)
            else:
                self.async_write_ha_state()

    async def _async_controller_pwm(self, now=None, force=False):
        """convert control output to pwm signal"""
        self._logger.debug(
            "Running pwm routine, routine=%s, forced=%s", now is not None, force
        )

        if self._emergency_stop:
            self._logger.warning(
                "PWM controller update: Cannot operate in emergency stop state, switch off"
            )

        if (
            self.control_output[ATTR_CONTROL_PWM_OUTPUT] in [None, 0]
            or self._hvac_on is None
            or self._emergency_stop
        ):
            self._async_cancel_pwm_routines()
            await self._async_switch_turn_off()
            return

        if self._hvac_on.get_pwm_time:
            pwm_duration = self._hvac_on.get_pwm_time.seconds
        else:
            pwm_duration = None

        if self._hvac_on.is_hvac_on_off_mode:
            if (
                self._is_valve_open()
                and self.control_output[ATTR_CONTROL_PWM_OUTPUT] <= 0
            ):
                await self._async_switch_turn_off()
            elif (
                not self._is_valve_open()
                and self.control_output[ATTR_CONTROL_PWM_OUTPUT] > 0
            ):
                await self._async_switch_turn_on()

        elif pwm_duration:
            now = time.time()
            self.update_pwm_time()

            pwm_scale = self._hvac_on.pwm_scale
            scale_factor = pwm_duration / pwm_scale
            start_time = (
                self._pwm_start_time
                + self.control_output[ATTR_CONTROL_OFFSET] * scale_factor
            )
            end_time = (
                self._pwm_start_time
                + min(sum(self.control_output.values()), self._hvac_on.pwm_scale)
                * scale_factor
            )

            # stop current schedules
            if self._start_pwm is not None:
                await self._async_start_pwm()
            if self._stop_pwm is not None:
                await self._async_stop_pwm()

            valve_open = self._is_valve_open()
            # check if current switch state is matching
            if self.control_output[ATTR_CONTROL_PWM_OUTPUT] == pwm_scale:
                self.hass.async_create_task(self._async_switch_turn_on())
            elif (start_time >= now or end_time <= now) and valve_open:
                self.hass.async_create_task(self._async_switch_turn_off())
            elif (start_time <= now < end_time) and not valve_open:
                self.hass.async_create_task(self._async_switch_turn_on())

            # schedule new switch changes
            if start_time > now:
                self.hass.async_create_task(self._async_start_pwm(start_time))
            if (
                end_time > now
                and self.control_output[ATTR_CONTROL_PWM_OUTPUT] != pwm_scale
            ):
                self.hass.async_create_task(self._async_stop_pwm(end_time))

        else:
            # proportional valve
            if (
                self._hvac_on.pwm_threshold
                > self.control_output[ATTR_CONTROL_PWM_OUTPUT]
                and self.switch_position > 0
            ):
                self.hass.async_create_task(self._async_switch_turn_off())
            elif self.switch_position != self.control_output[ATTR_CONTROL_PWM_OUTPUT]:
                self.hass.async_create_task(self._async_switch_turn_on())
        self.async_write_ha_state()

    @callback
    def _async_cancel_pwm_routines(self):
        """cancel scheduled switch routines"""
        if self._async_start_pwm is not None:
            self.hass.create_task(self._async_start_pwm())
        if self._async_stop_pwm is not None:
            self.hass.create_task(self._async_stop_pwm())

    async def _async_start_pwm(self, start_time=None):
        """
        start pwm at specified time
        """
        if start_time is None and self._start_pwm is not None:
            self._logger.debug("cancel scheduled switch on")
            self._start_pwm()
            self._start_pwm = None
        elif start_time is not None and self._start_pwm is not None:
            self._logger.debug("Re-define scheduled switch on")
            self._start_pwm()
            self._start_pwm = None
        if start_time and self._start_pwm is None:
            self._logger.debug("Define scheduled switch on")
            self._start_pwm = async_track_point_in_utc_time(
                self.hass,
                self.async_turn_switch_on_factory(),
                datetime.datetime.fromtimestamp(start_time),
            )
            self.async_on_remove(self._start_pwm)

    async def _async_stop_pwm(self, stop_time=None):
        """
        stop pwm at specified time
        """
        if stop_time is None and self._stop_pwm is not None:
            self._logger.debug("cancel scheduled switch off")
            self._stop_pwm()
            self._stop_pwm = None
        elif stop_time is not None and self._stop_pwm is not None:
            self._logger.debug("Re-define scheduled switch off")
            self._stop_pwm()
            self._stop_pwm = None
        if stop_time and self._stop_pwm is None:
            self._logger.debug("Define scheduled switch off")
            self._stop_pwm = async_track_point_in_utc_time(
                self.hass,
                self.async_turn_switch_off_factory(),
                datetime.datetime.fromtimestamp(stop_time),
            )
            self.async_on_remove(self._stop_pwm)

    @callback
    def async_turn_switch_on_factory(self, hvac_mode=None, control_val=None):
        """Generate turn on callbacks as factory."""

        async def async_turn_on_switch(now):
            """Turn on specific switch."""
            await self._async_switch_turn_on(
                hvac_mode=hvac_mode, control_val=control_val
            )

        return async_turn_on_switch

    async def _async_switch_turn_on(self, hvac_mode=None, control_val=None):
        """
        Open valve or reposition proportional valve
        NC/NO aware. NC conversion to NO
        """
        self._logger.debug("Turn ON")
        if hvac_mode:
            _hvac_on = self._hvac_def[hvac_mode]
        else:
            _hvac_on = self._hvac_on
            hvac_mode = self._hvac_mode

        if _hvac_on:
            entity_id = _hvac_on.get_hvac_switch
        else:
            entity_id = None

        if not entity_id:
            self._logger.debug("No switch defined for {}".format(hvac_mode))
            return

        if _hvac_on.is_hvac_switch_on_off:
            if self._is_valve_open(hvac_mode=hvac_mode):
                self._logger.debug("Switch already ON")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            self._logger.debug("Order 'ON' sent to switch device '%s'", entity_id)

            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                operation = SERVICE_TURN_ON
            else:
                operation = SERVICE_TURN_OFF
            await self.hass.services.async_call(
                HA_DOMAIN, operation, data, context=self._context
            )

        else:
            # valve mode
            if not control_val:
                control_val = self.control_output[ATTR_CONTROL_PWM_OUTPUT]

            if _hvac_on.get_hvac_switch_mode == NO_SWITCH_MODE:
                control_val = _hvac_on.pwm_scale - control_val

            self._logger.debug(
                "Change state of heater '%s' to '%s'",
                entity_id,
                control_val,
            )
            data = {
                ATTR_ENTITY_ID: entity_id,
                ATTR_VALUE: control_val,
            }

            method = entity_id.split(".")[0]

            await self.hass.services.async_call(
                method,
                SERVICE_SET_VALUE,
                data,
                context=self._context,
            )

    @callback
    def async_turn_switch_off_factory(self, hvac_mode=None):
        """Generate turn on callbacks as factory."""

        async def async_turn_off_switch(now):
            """Turn off specific switch."""
            await self._async_switch_turn_off(hvac_mode=hvac_mode)

        return async_turn_off_switch

    async def _async_switch_turn_off(self, hvac_mode=None):
        """
        Close valve
        NC/NO aware. NC converted to NO
        """
        self._logger.debug("Turn OFF called")
        if hvac_mode:
            _hvac_on = self._hvac_def[hvac_mode]
        else:
            hvac_mode = self._hvac_mode
            _hvac_on = self._hvac_on
        if _hvac_on:
            entity_id = _hvac_on.get_hvac_switch
        else:
            entity_id = None

        if not entity_id:
            self._logger.debug("No switch defined for {}".format(hvac_mode))
            return

        if _hvac_on.is_hvac_switch_on_off:
            if not self._is_valve_open(hvac_mode=hvac_mode):
                self._logger.debug("Switch already OFF")
                return

            data = {ATTR_ENTITY_ID: entity_id}
            self._logger.debug("Order 'OFF' sent to switch device '%s'", entity_id)

            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                operation = SERVICE_TURN_OFF
            else:
                operation = SERVICE_TURN_ON
            await self.hass.services.async_call(
                HA_DOMAIN, operation, data, context=self._context
            )
        else:
            # valve mode
            self._logger.debug(
                "Change state of switch '%s' to '%s'",
                entity_id,
                0,
            )

            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                control_val = 0
            else:
                control_val = _hvac_on.pwm_scale

            data = {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: control_val}

            method = entity_id.split(".")[0]

            await self.hass.services.async_call(
                method,
                SERVICE_SET_VALUE,
                data,
                context=self._context,
            )

            self._hvac_def[hvac_mode].stuck_loop = False

    async def _async_switch_idle(self, hvac_mode):
        """Bring switch to idle state"""
        self._logger.debug("Bring switch to default state")

        _hvac_on = self._hvac_def[hvac_mode]
        entity_id = _hvac_on.get_hvac_switch

        if _hvac_on.is_hvac_switch_on_off:
            data = {ATTR_ENTITY_ID: entity_id}
            operation = SERVICE_TURN_OFF
            await self.hass.services.async_call(
                HA_DOMAIN, operation, data, context=self._context
            )
        else:
            control_val = 0
            data = {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: control_val}
            method = entity_id.split(".")[0]
            await self.hass.services.async_call(
                method,
                SERVICE_SET_VALUE,
                data,
                context=self._context,
            )

    async def _async_toggle_switch(self, hvac_mode: HVACMode, entity_id):
        """toggle the state of a switch temporarily and hereafter set it to 0 or 1"""
        DURATION = 60
        if self._hvac_on is None or (
            self._hvac_on and self._is_valve_open() is not True
        ):
            self._hvac_def[hvac_mode].stuck_loop = True
            if self._is_valve_open(hvac_mode=hvac_mode):
                self._logger.info(
                    "switch '%s' toggle state temporarily to OFF for %s sec"
                    % (entity_id, DURATION)
                )
                await self._async_switch_turn_off(hvac_mode=hvac_mode)
                # await asyncio.sleep(DURATION)

                # await self._async_switch_turn_on(
                #     hvac_mode=hvac_mode, control_val=self._hvac_def[hvac_mode].pwm_scale
                # )
                async_track_point_in_utc_time(
                    self.hass,
                    self.async_turn_switch_on_factory(hvac_mode=hvac_mode),
                    datetime.datetime.fromtimestamp(time.time() + DURATION),
                )
            else:
                self._logger.info(
                    "switch '%s' toggle state temporarily to ON for %s sec"
                    % (entity_id, DURATION)
                )
                if self._hvac_def[hvac_mode].get_hvac_switch_mode == NC_SWITCH_MODE:
                    control_val = 0
                else:
                    control_val = self._hvac_def[hvac_mode].pwm_scale

                await self._async_switch_turn_on(
                    hvac_mode=hvac_mode, control_val=control_val
                )
                # BUG: timedelta
                async_track_point_in_utc_time(
                    self.hass,
                    self.async_turn_switch_off_factory(hvac_mode=hvac_mode),
                    datetime.datetime.fromtimestamp(time.time() + DURATION),
                )

    @callback
    def _async_activate_emergency_stop(self, source, sensor):
        """Send an emergency OFF order to HVAC switch."""
        if sensor not in self._emergency_stop:
            self._logger.warning(
                "Emergency OFF order send from {} due to sensor {}".format(
                    source, sensor
                )
            )
            self._emergency_stop.append(sensor)
            # cancel scheduled switch routines
            self._async_cancel_pwm_routines()
            self.hass.create_task(self._async_switch_turn_off())
            self.hass.create_task(self.async_set_preset_mode(PRESET_EMERGENCY))
        else:
            self._logger.debug("Emergency OFF recall send from {}".format(source))

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode."""
        if preset_mode not in self.preset_modes and preset_mode not in [
            PRESET_NONE,
            PRESET_EMERGENCY,
            PRESET_RESTORE,
        ]:
            self._logger.error(
                "This preset (%s) is not enabled (see the configuration)", preset_mode
            )
            return
        if preset_mode == PRESET_EMERGENCY:
            self._logger.debug("Preset changed due to mergency mode")

            self._old_preset = self._preset_mode
        if preset_mode == PRESET_RESTORE:
            preset_mode = self._old_preset

        self._preset_mode = preset_mode
        if self._hvac_on:
            self._hvac_on.preset_mode = preset_mode
            self._logger.debug("Set preset mode to '%s'", preset_mode)

        if self.is_master:
            await self._async_set_satelite_preset(preset_mode)

        if self._hvac_on:
            self.hass.async_create_task(self._async_controller(force=True))

        self.async_write_ha_state()

    async def _async_set_satelite_preset(self, preset_mode):
        """change preset mode at satelites"""
        for sat in self._hvac_on.get_satelites:
            await self.hass.services.async_call(
                "multizone_thermostat",
                "set_preset_mode",
                {
                    ATTR_ENTITY_ID: "climate." + sat,
                    ATTR_PRESET_MODE: preset_mode,
                },
                context=self._context,
                # blocking=False,
            )

    def _is_valve_open(self, hvac_mode=None):
        """
        If the valve is open.
        NC/NO aware. NO converted to NC
        """
        if hvac_mode:
            _hvac_on = self._hvac_def[hvac_mode]
        else:
            _hvac_on = self._hvac_on
            hvac_mode = self._hvac_mode
        if _hvac_on:
            entity_id = _hvac_on.get_hvac_switch
        else:
            entity_id = None

        if not entity_id:
            self._logger.debug("no found entity for {}".format(hvac_mode))
            return None

        switch_state = self.hass.states.get(entity_id).state
        # check if error state or to restore from error state
        if switch_state in ERROR_STATE or (
            not _hvac_on.is_hvac_switch_on_off and not is_float(switch_state)
        ):
            self.hass.create_task(
                self._async_activate_emergency_stop(
                    "active switch state check", sensor=entity_id
                )
            )
            return None
        else:
            if entity_id in self._emergency_stop:
                self._emergency_stop.remove(entity_id)
                if self._emergency_stop == []:
                    self.hass.create_task(self.async_set_preset_mode(PRESET_RESTORE))

            if _hvac_on.is_hvac_switch_on_off:
                if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                    if switch_state == STATE_ON:
                        return True
                    else:
                        return False
                else:
                    if switch_state == STATE_OFF:
                        return True
                    else:
                        return False
            else:
                if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                    valve_position = float(switch_state)
                else:
                    valve_position = _hvac_on.pwm_scale - float(switch_state)

                if valve_position > 0:
                    return True
                else:
                    return False

    @property
    def switch_position(self):
        """
        get state of switch.
        NC/NO aware. NO converted to NC
        """
        entity_id = self._hvac_on.get_hvac_switch
        sensor_state = self.hass.states.get(entity_id)

        if not sensor_state:
            return False
        try:
            valve_position = float(sensor_state.state)
            if self._hvac_on.get_hvac_switch_mode == NO_SWITCH_MODE:
                valve_position = self._hvac_on.pwm_scale - valve_position
            return valve_position
        except:  # pylint: disable=bare-except
            self._logger.error(
                "not able to get position of {}, current state is {}".format(
                    entity_id, sensor_state.state
                )
            )

    @property
    def supported_features(self):
        """Return the list of supported features."""
        if self.is_master:
            return ClimateEntityFeature.PRESET_MODE
        else:
            if self.preset_modes == [PRESET_NONE]:
                return ClimateEntityFeature.TARGET_TEMPERATURE
            return (
                ClimateEntityFeature.PRESET_MODE
                | ClimateEntityFeature.TARGET_TEMPERATURE
            )

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
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        if self._hvac_on:
            if self.is_master:
                return None
            if self._hvac_mode != HVACMode.OFF:
                if self.preset_mode == PRESET_AWAY:
                    return self._hvac_on.get_away_temp
                elif self._hvac_on.min_target_temp:
                    return self._hvac_on.min_target_temp
        else:
            return None

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if self._hvac_on:
            if self.is_master:
                return None
            elif self._hvac_mode != HVACMode.OFF:
                if self.preset_mode == PRESET_AWAY:
                    return self._hvac_on.get_away_temp
                elif self._hvac_on.max_target_temp:
                    return self._hvac_on.max_target_temp
        else:
            return None

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        if self._hvac_on:
            if self.is_master:
                return None

        if not self._kf_temp:
            return self._current_temperature
        else:
            return round(self._kf_temp.get_temp, 3)

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

        Need to be one of HVACAction.*.
        """
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        elif self._hvac_mode == HVACMode.COOL:
            if self._is_valve_open():
                return HVACAction.COOLING
            else:
                return HVACAction.IDLE
        elif self._hvac_mode == HVACMode.HEAT:
            if self._is_valve_open():
                return HVACAction.HEATING
            else:
                return HVACAction.IDLE

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if (
            self._hvac_mode is HVACMode.OFF
            or self._hvac_mode is None
            or self._hvac_on is None
            or self.is_master
        ):
            return None
        return self._hvac_on.target_temperature

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return self._enabled_hvac_mode + [HVACMode.OFF]

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp."""
        return self._preset_mode

    @property
    def preset_modes(self):
        """Return a list of available preset modes."""
        modes = [PRESET_NONE]
        if self._hvac_on:
            if self._hvac_on.get_away_temp or self.is_master:
                modes = modes + [PRESET_AWAY]

        return modes

    @property
    def filter_mode(self):
        """Return the UKF mode."""
        return self._filter_mode

    @filter_mode.setter
    def filter_mode(self, mode):
        """Set the UKF mode."""
        self._filter_mode = mode


def is_float(element):
    """check if input is float"""
    try:
        float(element)
        return True
    except ValueError:
        return False
