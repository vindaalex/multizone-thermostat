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
from datetime import timedelta
from typing import Callable, Dict
import time
import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateEntity
from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, callback
from homeassistant.helpers import condition, entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
    async_track_utc_time_change,
)
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify
import homeassistant.util.dt as dt_util

from . import DOMAIN, PLATFORMS
from . import hvac_setting
from . import UKF_config

from .const import *

# from defaults_thermostat import *
# from defaults_controller_input import *
# from defaults_controller import *


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


PID_control_options_opt = {
    vol.Optional(CONF_KP): vol.Coerce(float),
    vol.Optional(CONF_KI): vol.Coerce(float),
    vol.Optional(CONF_KD): vol.Coerce(float),
    vol.Optional(CONF_MIN_DIFFERENCE): vol.Coerce(float),
    vol.Optional(CONF_MAX_DIFFERENCE): vol.Coerce(float),
    vol.Optional(CONF_WINDOW_OPEN_TEMPDROP): vol.Coerce(float),
}

PID_control_options_req = {
    vol.Required(CONF_KP): vol.Coerce(float),
    vol.Required(CONF_KI): vol.Coerce(float),
    vol.Required(CONF_KD): vol.Coerce(float),
    vol.Optional(CONF_MIN_DIFFERENCE): vol.Coerce(float),
    vol.Optional(CONF_WINDOW_OPEN_TEMPDROP): vol.Coerce(float),
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

hvac_control_heat = {
    **hvac_control_options,
    vol.Required(CONF_HVAC_MODE_MIN_TEMP, default=DEFAULT_MIN_TEMP_HEAT): vol.Coerce(
        float
    ),
    vol.Required(CONF_HVAC_MODE_MAX_TEMP, default=DEFAULT_MAX_TEMP_HEAT): vol.Coerce(
        float
    ),
    vol.Required(
        CONF_HVAC_MODE_INIT_TEMP, default=DEFAULT_TARGET_TEMP_HEAT
    ): vol.Coerce(float),
}
hvac_control_cool = {
    **hvac_control_options,
    vol.Required(CONF_HVAC_MODE_MIN_TEMP, default=DEFAULT_MIN_TEMP_COOL): vol.Coerce(
        float
    ),
    vol.Required(CONF_HVAC_MODE_MAX_TEMP, default=DEFAULT_MAX_TEMP_COOL): vol.Coerce(
        float
    ),
    vol.Required(
        CONF_HVAC_MODE_INIT_TEMP, default=DEFAULT_TARGET_TEMP_COOL
    ): vol.Coerce(float),
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
            vol.Optional(CONF_SENSOR_FILTER, default=DEFAULT_SENSOR_FILTER): vol.Coerce(
                int
            ),
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
            vol.Optional(HVAC_MODE_HEAT): vol.Schema(hvac_control_heat),
            vol.Optional(HVAC_MODE_COOL): vol.Schema(hvac_control_cool),
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
    filter_mode = config.get(CONF_SENSOR_FILTER)
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
        # hvac_def["heat"] = hvac_setting.HVACSetting(name, HVAC_MODE_HEAT, heat_conf)
    if cool_conf:
        enabled_hvac_modes.append(HVAC_MODE_COOL)
        hvac_def[HVAC_MODE_COOL] = cool_conf
        # hvac_def["cool"] = hvac_setting.HVACSetting(name, HVAC_MODE_COOL, cool_conf)

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
        filter_mode,
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
        self._logger = logging.getLogger().getChild(
            "multizone_thermostat." + self._name
        )
        self._logger.info("initialise: %s", self._name)
        self._sensor_entity_id = sensor_entity_id
        self._filter_mode = filter_mode
        self._sensor_out_entity_id = sensor_out_entity_id
        self._temp_precision = precision
        self._unit = unit
        self._hvac_def = {}
        for mode, mode_config in hvac_def.items():
            self._hvac_def[mode] = hvac_setting.HVACSetting(
                self._logger.name, mode, mode_config
            )
        self._hvac_mode = None
        self._hvac_mode_init = initial_hvac_mode
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
        self._outdoor_temperature = None
        self._old_mode = "off"
        self._hvac_on = None
        self._current_alive_time = None
        self._satelites = None
        self._kf_temp = None
        self.time_changed = None
        self.control_output = None

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
        self._logger.info("init thermostat")
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
            _dt = dt_util.utcnow() + timedelta(hours=24)
            async_track_utc_time_change(
                self.hass,
                self.prevent_stuck_switch,
                hour=_dt.hour,
                minute=_dt.minute,
                second=_dt.second,
            )

        @callback
        async def _async_startup(*_):
            """Init on startup."""
            if self._sensor_entity_id:
                sensor_state = self.hass.states.get(self._sensor_entity_id)
            else:
                sensor_state = None
            if sensor_state and sensor_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                await self._async_update_current_temp(sensor_state.state)
                # setup filter after frist temp reading
                if not self._kf_temp and self.filter_mode > 0:
                    await self.async_set_filter_mode(self.filter_mode)

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
            await _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        # Check if we have an old state, if so, restore it
        old_state = await self.async_get_last_state()
        if not self._enable_old_state:
            # init in case no restore is required
            if not self._hvac_mode_init:
                self._logger.warning("no initial hvac mode specified: force off mode")
                self._hvac_mode_init = HVAC_MODE_OFF
            self._logger.info("init default hvac mode: %s", self._hvac_mode_init)
        else:
            await self.async_restore_old_state(old_state)

        await self.async_set_hvac_mode(self._hvac_mode_init)
        self.async_write_ha_state()

    async def async_restore_old_state(self, old_state):
        """function to restore old state/config"""
        self._logger.debug("Old state stored : %s", old_state)

        try:
            if old_state is None:
                self._hvac_mode_init = HVAC_MODE_OFF
                raise ValueError("No old state, init in default off mode")

            old_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
            if old_preset_mode is None:
                old_preset_mode = "none"
            old_hvac_mode = old_state.state
            old_temperature = old_state.attributes.get(ATTR_TEMPERATURE)
            self._logger.debug(
                "Old state preset mode %s, hvac mode %s, temperature %s",
                old_preset_mode,
                old_hvac_mode,
                old_temperature,
            )

            if (
                old_hvac_mode is None
                or old_preset_mode not in self.preset_modes
                or old_hvac_mode not in self.hvac_modes
                or "hvac_def" not in old_state.attributes
            ):
                raise ValueError("Invalid old state, init in default off mode")

            self._logger.info("restore old controller settings")
            self._hvac_mode_init = old_hvac_mode
            self._preset_mode = old_preset_mode

            if self._hvac_mode_init != HVAC_MODE_OFF:
                old_def = old_state.attributes["hvac_def"]
                for key, data in old_def.items():
                    if key in list(self._hvac_def.keys()):
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
            self._hvac_mode_init = HVAC_MODE_OFF
            self._logger.warning("restoring old state failed:%s", str(eror))
            return

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
        self._logger.info(
            "new minimum PID difference for %s to: %s", hvac_mode, min_diff
        )
        self._hvac_def[hvac_mode].min_diff(min_diff)
        self.async_write_ha_state()

    async def async_set_pid(
        self, hvac_mode, control_mode, kp=None, ki=None, kd=None, update=False
    ):  # pylint: disable=invalid-name
        """Set new PID Controller Kp,Ki,Kd value."""
        self._logger.info(
            "new PID for %s %s to: %s;%s;%s", hvac_mode, control_mode, kp, ki, kd
        )
        self._hvac_def[hvac_mode].set_pid_param(
            control_mode, kp=kp, ki=ki, kd=kd, update=update
        )
        self.async_write_ha_state()

    async def async_set_filter_mode(self, mode):
        """Set new filter for the temp sensor."""
        self._filter_mode = mode
        self._logger.info("modified sensor filter mode to: %s", mode)

        if mode == 0:
            self._current_temperature = self.current_temperature
            self._kf_temp = None
            if self._hvac_on:
                self._hvac_on.current_state = None
                self._hvac_on.current_temperature = self.current_temperature
        else:
            # if self._hvac_on:
            #     cycle_time = self._hvac_on.get_operate_cycle_time.seconds
            # else:
            cycle_time = (
                60  # dt is variable in . Need to make variable in future iteration?
            )

            if not self._kf_temp:
                if self._current_temperature:
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
        self.async_write_ha_state()

    async def async_set_integral(self, hvac_mode, control_mode, integral):
        """Set new PID Controller integral value."""
        self._logger.info(
            "new PID integral for %s %s to: %s", hvac_mode, control_mode, integral
        )
        self._hvac_def[hvac_mode].integral(control_mode, integral)
        self.async_write_ha_state()

    async def async_set_goal(self, hvac_mode, goal):
        """Set new valve Controller goal value."""
        self._logger.info("new PID valve goal for %s to: %s", hvac_mode, goal)
        self._hvac_def[hvac_mode].goal(goal)
        self.async_write_ha_state()

    async def async_set_ka_kb(
        self, hvac_mode, ka=None, kb=None
    ):  # pylint: disable=invalid-name
        """Set new weather Controller ka,kb value."""
        self._logger.info("new weatehr ka,kb %s to: %s;%s", hvac_mode, ka, kb)
        self._hvac_def[hvac_mode].set_ka_kb(ka=ka, kb=kb)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set hvac mode."""
        # No changes have been made
        if self._hvac_mode == hvac_mode:
            return
        if hvac_mode not in self.hvac_modes:
            self._logger.error("Unrecognized hvac mode: %s", hvac_mode)
            return
        self._logger.info("HVAC mode changed to %s", hvac_mode)
        self._old_mode = self._hvac_mode
        self._hvac_mode = hvac_mode

        if self._hvac_on:
            # restore preset mode
            self._preset_mode = PRESET_NONE
            # stop keep_live
            await self._async_update_keep_alive()
            # stop tracking satelites
            if self._hvac_on.is_master_mode:
                await self._async_track_satelites()

        # new hvac mode thus all switches off
        for key, _ in self._hvac_def.items():
            await self._async_switch_turn_off(hvac_mode=key)

        if self._hvac_mode == HVAC_MODE_OFF:
            self._logger.info("HVAC mode is OFF. Turn the devices OFF and exit")
            self._hvac_on = None
            self.async_write_ha_state()
            return
        else:
            self._hvac_on = self._hvac_def[self._hvac_mode]

            # reset time stamp pid to avoid integral run-off
            if self._hvac_on.is_hvac_proportional_mode:
                self.time_changed = time.time()

                if self._hvac_on.is_hvac_pid_mode or self._hvac_on.is_hvac_valve_mode:
                    self._hvac_on.pid_reset_time()

                # start listening for outdoor sensors
                if self._hvac_on.is_hvac_wc_mode and self.outdoor_temperature:
                    self._hvac_on.outdoor_temperature = self.outdoor_temperature

            # start listener for satelite thermostats
            if self._hvac_on.is_master_mode:
                await self._async_track_satelites(
                    entity_list=self._hvac_on.get_satelites
                )
            # else:
            #     await self._async_update_current_temp()
            # self._hvac_on.current_temperature = self.current_temperature

            # update listener
            await self._async_update_keep_alive(self._hvac_on.get_operate_cycle_time)
            await self._async_operate()

            # Ensure we update the current operation after changing the mode
            self.async_write_ha_state()

    async def _async_update_keep_alive(self, interval=None):
        """run main controller at specified interval"""
        self._logger.debug("update 'keep alive' for %s", self._hvac_mode)
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
        self._logger.debug(
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
            self._logger.warning(
                "Try to update temperature to %s for mode %s but this mode is not enabled",
                temperature,
                hvac_mode,
            )
            return

        if hvac_mode is None or hvac_mode == HVAC_MODE_OFF:
            self._logger.warning("You cannot update temperature for OFF mode")
            return

        self._logger.debug(
            "Temperature updated to %s for mode %s", temperature, hvac_mode
        )

        if (
            self.preset_mode == PRESET_AWAY
        ):  # when preset mode is away, change the temperature but do not operate
            self._logger.debug(
                "Preset mode away when temperature is updated : skipping operate"
            )
            return

        self._hvac_on.target_temperature = temperature
        # self._target_temp = temperature

        if self._hvac_mode != HVAC_MODE_OFF:
            await self._async_operate(force=True)

        self.async_write_ha_state()

    async def _async_sensor_temperature_changed(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        self._logger.debug("Sensor temperature updated to %s", new_state.state)
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._logger.warning(
                "Sensor temperature {} invalid: {}".format(
                    new_state.name, new_state.state
                )
            )
            await self._async_activate_emergency_stop(new_state.name)
            return

        await self._async_update_current_temp(new_state.state)

        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if self._hvac_mode != HVAC_MODE_OFF and self._hvac_mode is not None:
            if self._hvac_on.is_hvac_on_off_mode:
                await self._async_operate(sensor_changed=True)

        self.async_write_ha_state()

    async def _async_sensor_outdoor_temperature_changed(self, event):
        """Handle outdoor temperature changes."""
        new_state = event.data.get("new_state")
        self._logger.debug("Sensor outdoor temperature updated to %s", new_state.state)
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._logger.warning(
                "Sensor temperature {} invalid {}".format(
                    new_state.name, new_state.state
                )
            )
            await self._async_activate_emergency_stop(new_state.name)
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
            self._logger.error("error receiving thermostat update. 'None' received")
            return
        self._logger.debug(
            "receiving thermostat %s update. new state: %s",
            new_state.name,
            new_state.state,
        )
        self._send_satelite(new_state)

        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if self._hvac_mode != HVAC_MODE_OFF:
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
                self._logger.debug(
                    "Time is %s, last changed is %s, stale duration is %s , limit is %s"
                    % (
                        datetime.datetime.now(datetime.timezone.utc),
                        sensor_state.last_updated,
                        datetime.datetime.now(datetime.timezone.utc)
                        - sensor_state.last_updated,
                        self._sensor_stale_duration,
                    )
                )
                self._logger.warning(
                    "Sensor %s has stalled, call the emergency stop" % (entity_id)
                )
                await self._async_activate_emergency_stop(entity_id)

            return

    @callback
    def prevent_stuck_switch(self, now):
        """Check if the switch has not changed for a cetrain period andforce operation to avoid stuck or jammed."""
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
                self._logger.warning(
                    "Switch %s stuck prevention activated: not changed state for %s"
                    % (
                        data[0],
                        datetime.datetime.now(datetime.timezone.utc)
                        - sensor_state.last_updated,
                    )
                )
                self.hass.async_create_task(
                    self._async_toggle_switch(hvac_mode, data[0])
                )
                # self._async_toggle_switch(hvac_def, data[0])

    @callback
    async def _async_switch_device_changed(self, event):
        """Handle device switch state changes."""
        new_state = event.data.get("new_state")
        entity_id = event.data.get("entity_id")
        self._logger.debug(
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

            for hvac_mode, data in self._hvac_def.items():

                if (
                    not data.stuck_loop
                    and data.get_hvac_switch == entity_id
                    and self._is_switch_active(hvac_mode=hvac_mode)
                ):
                    self._logger.warning(
                        "No swithces should be 'on' in 'off' mode: switch of %s changed has to %s. Force off",
                        entity_id,
                        new_state.state,
                    )
                    await self._async_switch_turn_off(hvac_mode=hvac_mode, force=True)

        if self._hvac_on:
            if entity_id != self._hvac_on.get_hvac_switch:
                self._logger.warning(
                    "Wrong switch of %s changed from %s",
                    entity_id,
                    new_state.state,
                )

        if new_state is None:
            return
        self.async_write_ha_state()

    async def _async_update_current_temp(self, current_temp=None):
        """Update thermostat, optionally with latest state from sensor."""
        if self._emergency_stop:
            self._logger.info(
                "Recover from emergency mode, new temperature updated to %s",
                current_temp,
            )
            self._emergency_stop = False

        if current_temp:
            self._logger.debug("Current temperature updated to %s", current_temp)
            # store local in case current hvac mode is off
            self._current_temperature = float(current_temp)

        try:
            if self._kf_temp:
                self._kf_temp.kf_predict()
                if current_temp:
                    tmp_temperature = float(current_temp)
                elif self._current_temperature:
                    tmp_temperature = self._current_temperature
                else:
                    tmp_temperature = None

                if tmp_temperature:
                    self._kf_temp.kf_update(tmp_temperature)

                self._logger.debug("kp update temp %s", self._kf_temp.get_temp)

            self.async_write_ha_state()
        except ValueError as ex:
            self._logger.error("Unable to update from sensor: %s", ex)

    async def _async_update_controller_temp(self):
        """Update temperature to controller routines."""
        if self._hvac_on:
            if not self._kf_temp:
                self._hvac_on.current_temperature = self._current_temperature
            else:
                if not self._hvac_on.is_master_mode:
                    self._hvac_on.current_state = [
                        self._kf_temp.get_temp,
                        self._kf_temp.get_vel,
                    ]

    def _update_outdoor_temperature(self, current_temp=None):
        """Update thermostat with latest state from outdoor sensor."""
        if self._emergency_stop:
            self._logger.info(
                "Recover from emergency mode, new outdoor temperature updated to %s",
                current_temp,
            )
            self._emergency_stop = False
        try:
            if current_temp:
                self._logger.debug(
                    "Current outdoor temperature updated to %s", current_temp
                )
                self._outdoor_temperature = float(current_temp)
                if self._hvac_on:
                    self._hvac_on.outdoor_temperature = self._outdoor_temperature
        except ValueError as ex:
            self._logger.error("Unable to update from sensor: %s", ex)

    async def _async_operate(self, now=None, sensor_changed=False, force=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            # time is passed by to the callback the async_track_time_interval function , and is set to "now"
            keepalive = now is not None  # boolean

            if self._emergency_stop:
                if keepalive:
                    self._logger.debug(
                        "Emergency stop active, exit routine. Re-send emergency stop"
                    )
                    await self._async_activate_emergency_stop("operate")
                else:
                    self._logger.warning("Cannot operate in emergency stop state")
                return

            if not self._hvac_on:
                return

            # update and check current temperatures
            if not sensor_changed and not self._hvac_on.is_master_mode:
                await self._async_update_current_temp()
                await self._async_update_controller_temp()

            if self._hvac_on.current_temperature is None:
                self._logger.warning("Current temp is None, cannot compare with target")
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
                        self._logger.debug(
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

                self._logger.debug(
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
            else:
                # when mode is pwm: calculate control output
                self._logger.debug("update controller")
                self._hvac_on.calculate(force)
                self.control_output = self._hvac_on.get_control_output
                self._logger.debug(
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
                self._logger.debug(
                    "Time exceeds 'on-time' by %s sec: turn off: %s",
                    entity_id,
                    round(time_on - time_passed, 0),
                )

                await self._async_switch_turn_off()
                self.time_changed = time.time()
            else:
                self._logger.debug(
                    "Time until %s turns off: %s sec", entity_id, time_on - time_passed
                )
        else:
            if time_off < time_passed:
                self._logger.debug(
                    "Time finshed 'off-time' by %s sec: turn on: %s",
                    entity_id,
                    round(time_passed - time_off, 0),
                )

                await self._async_switch_turn_on()
                self.time_changed = time.time()
            else:
                self._logger.debug(
                    "Time until %s turns on: %s sec", entity_id, time_off - time_passed
                )

    async def _async_switch_turn_on(
        self, hvac_mode=None, control_val=None, force=False
    ):
        """Turn switch toggleable device on."""
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
            if self._is_switch_active(hvac_mode=hvac_mode) and not force:
                self._logger.debug("Switch already ON")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            self._logger.debug("Order ON sent to switch device %s", entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
            )
        else:
            # valve mode
            if not control_val:
                control_val = self.control_output

            self._logger.debug(
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

    async def _async_switch_turn_off(self, hvac_mode=None, force=False):
        """Turn toggleable device off."""
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
            if not self._is_switch_active(hvac_mode=hvac_mode) and not force:
                self._logger.debug("Switch already OFF")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            self._logger.debug("Order OFF sent to switch device %s", entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
            )
        else:
            # valve mode
            self._logger.debug(
                "Change state of switch %s to %s",
                entity_id,
                0,
            )
            data = {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 0}
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_toggle_switch(self, hvac_mode, entity_id):
        """toggle the state of a switch temporarily and hereafter set it to 0 or 1"""
        self._hvac_def[hvac_mode].stuck_loop = True

        if self._is_switch_active(hvac_mode=hvac_mode):
            self._logger.info(
                "switch %s toggle state temporarily to OFF for 3min" % (entity_id)
            )
            await self._async_switch_turn_off(hvac_mode=hvac_mode, force=True)
            await asyncio.sleep(3 * 60)
            await self._async_switch_turn_on(
                hvac_mode=hvac_mode, control_val=100, force=True
            )
        else:
            self._logger.info(
                "switch %s toggle state temporarily to ON for 3min" % (entity_id)
            )
            await self._async_switch_turn_on(
                hvac_mode=hvac_mode, control_val=100, force=True
            )
            await asyncio.sleep(3 * 60)
            await self._async_switch_turn_off(hvac_mode=hvac_mode, force=True)

        self._hvac_def[hvac_mode].stuck_loop = False

    async def _async_activate_emergency_stop(self, source):
        """Send an emergency OFF order to HVAC switch."""
        self._logger.warning("Emergency OFF order send due to:{}".format(source))
        self._emergency_stop = True
        await self._async_switch_turn_off(force=True)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.
        This method must be run in the event loop and returns a coroutine.
        """
        if preset_mode not in self.preset_modes and preset_mode != PRESET_NONE:
            self._logger.error(
                "This preset (%s) is not enabled (see the configuration)", preset_mode
            )
            return

        self._preset_mode = preset_mode
        self._hvac_on.preset_mode = preset_mode
        self._logger.debug("Set preset mode to %s", preset_mode)

        await self._async_operate(force=True)
        self.async_write_ha_state()

    def _is_switch_active(self, hvac_mode=None):
        """If the toggleable switch device is currently active."""
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
            return False

        if _hvac_on.is_hvac_switch_on_off:
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
            except:  # pylint: disable=bare-except
                self._logger.error(
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
        if self._hvac_on:
            if self._hvac_mode != HVAC_MODE_OFF:
                if self.preset_mode == PRESET_AWAY:
                    return self._hvac_on.get_away_temp
                elif self._hvac_on.min_target_temp:
                    return self._hvac_on.min_target_temp

            # Get default temp from super class
            # return super().min_temp
        else:
            return None

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if self._hvac_on:
            if self._hvac_mode != HVAC_MODE_OFF:
                if self.preset_mode == PRESET_AWAY:
                    return self._hvac_on.get_away_temp
                elif self._hvac_on.max_target_temp:
                    return self._hvac_on.max_target_temp

        # Get default temp from super class
        # return super().max_temp
        else:
            return None

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
        if (
            self._hvac_mode is HVAC_MODE_OFF
            or self._hvac_mode is None
            or self._hvac_on is None
        ):
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

        return modes

    @property
    def filter_mode(self):
        """Return the UKF mode."""
        return self._filter_mode

    @filter_mode.setter
    def filter_mode(self, mode):
        """Set the UKF mode."""
        self._filter_mode = mode
