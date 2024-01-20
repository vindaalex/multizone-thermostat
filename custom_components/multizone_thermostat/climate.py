"""MultiZone thermostat.

Incl support for:
- multizone heating
- UKF filter on sensor
- various controllers:
    - temperature: PID
    - outdoor temperature: weather
    - valve position: PID
For more details about this platform, please read to the README
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    EVENT_HOMEASSISTANT_START,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_JAMMED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_OPENING,
    STATE_PROBLEM,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, HomeAssistant, callback
from homeassistant.exceptions import ConditionError
from homeassistant.helpers import condition
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.template import state_attr
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType, EventType

from . import DOMAIN, PLATFORMS, UKF_config, hvac_setting, services
from .const import (
    ATTR_CONTROL_MODE,
    ATTR_CONTROL_OFFSET,
    ATTR_CONTROL_OUTPUT,
    ATTR_CONTROL_PWM_OUTPUT,
    ATTR_CURRENT_OUTDOOR_TEMPERATURE,
    ATTR_CURRENT_TEMP_VEL,
    ATTR_EMERGENCY_MODE,
    ATTR_FILTER_MODE,
    ATTR_HVAC_DEFINITION,
    ATTR_SELF_CONTROLLED,
    ATTR_STUCK_LOOP,
    ATTR_VALUE,
    CLOSE_TO_PWM,
    CONF_AREA,
    CONF_DETAILED_OUTPUT,
    CONF_ENABLE_OLD_INTEGRAL,
    CONF_ENABLE_OLD_PARAMETERS,
    CONF_ENABLE_OLD_STATE,
    CONF_EXTRA_PRESETS,
    CONF_FILTER_MODE,
    CONF_INITIAL_HVAC_MODE,
    CONF_INITIAL_PRESET_MODE,
    CONF_MASTER_MODE,
    CONF_PASSIVE_CHECK_TIME,
    CONF_PASSIVE_SWITCH_CHECK,
    CONF_PRECISION,
    CONF_PWM_SCALE,
    CONF_SENSOR,
    CONF_SENSOR_OUT,
    CONF_STALE_DURATION,
    CONTROL_START_DELAY,
    MASTER_CONTROL_LEAD,
    NC_SWITCH_MODE,
    NO_SWITCH_MODE,
    PRESET_EMERGENCY,
    PRESET_RESTORE,
    PWM_LAG,
    SAT_CONTROL_LEAD,
    SERVICE_SET_VALUE,
    START_MISALINGMENT,
    OperationMode,
)
from .platform_schema import PLATFORM_SCHEMA  # noqa: F401

ERROR_STATE = [STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_JAMMED, STATE_PROBLEM]
NOT_SUPPORTED_SWITCH_STATES = [STATE_OPEN, STATE_OPENING, STATE_CLOSED, STATE_CLOSING]
HVAC_ACTIVE = [HVACMode.HEAT, HVACMode.COOL]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the multizone thermostat platform."""

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
    passive_switch_time = config.get(CONF_PASSIVE_CHECK_TIME)
    detailed_output = config.get(CONF_DETAILED_OUTPUT)
    enable_old_state = config.get(CONF_ENABLE_OLD_STATE)
    enable_old_parameters = config.get(CONF_ENABLE_OLD_PARAMETERS)
    enable_old_integral = config.get(CONF_ENABLE_OLD_INTEGRAL)
    heat_conf = config.get(HVACMode.HEAT)
    cool_conf = config.get(HVACMode.COOL)

    hvac_def = {}
    custom_presets = []
    enabled_hvac_modes = []

    # Append the enabled hvac modes to the list
    if heat_conf:
        enabled_hvac_modes.append(HVACMode.HEAT)
        hvac_def[HVACMode.HEAT] = heat_conf
        custom_presets.append(list(heat_conf.get(CONF_EXTRA_PRESETS).keys()))
    if cool_conf:
        enabled_hvac_modes.append(HVACMode.COOL)
        hvac_def[HVACMode.COOL] = cool_conf
        custom_presets.append(list(cool_conf.get(CONF_EXTRA_PRESETS).keys()))

    custom_presets = list({key_i for list_i in custom_presets for key_i in list_i})
    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)
    services.register_services(list(set(custom_presets)))

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
                passive_switch_time,
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
        passive_switch_time,
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
        self._passive_switch_time = passive_switch_time
        self._area = area
        self._emergency_stop = []
        self._current_temperature = None
        self._outdoor_temperature = None
        self._old_mode = "off"
        self._hvac_on = None
        self._loop_controller = None
        self._loop_pwm = None
        self._start_pwm = None
        self._stop_pwm = None
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
                detailed_output,
            )

        self._logger = logging.getLogger(DOMAIN).getChild(name)

        if unique_id is not None:
            self._attr_unique_id = unique_id
        else:
            self._attr_unique_id = None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added.

        Attach the listeners.
        """
        self._logger.info("Add thermostat to hass")
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
                hour=self._passive_switch_time.hour,
                minute=self._passive_switch_time.minute,
                second=self._passive_switch_time.second,
            )

        async def _async_startup(*_) -> None:
            """Init on startup."""
            self._logger.debug("Run start-up")
            save_state = False

            # read room temperature sensor
            if self._sensor_entity_id:
                sensor_state = self.hass.states.get(self._sensor_entity_id)
            else:
                sensor_state = None

            # process room temperature
            if sensor_state and sensor_state.state not in ERROR_STATE:
                await self._async_update_current_temp(sensor_state.state)
                save_state = True

            # check outdoor temperature
            if self._sensor_out_entity_id:
                sensor_state = self.hass.states.get(self._sensor_out_entity_id)
            else:
                sensor_state = None

            # process outdoor temperature
            if sensor_state and sensor_state.state not in ERROR_STATE:
                self._async_update_outdoor_temperature(sensor_state.state)
                save_state = True

            # sate the current state
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

        if self.hass.state == CoreState.running:
            await _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

    def restore_old_state(self, old_state) -> None:
        """Restore old state/config."""
        self._logger.debug("Old state stored : '%s'", old_state)

        try:
            old_hvac_mode = old_state.state
            old_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE, PRESET_NONE)
            old_temperature = old_state.attributes.get(ATTR_TEMPERATURE)
            self._logger.debug(
                "Old state preset mode %s, hvac mode %s, temperature set point '%s'",
                old_preset_mode,
                old_hvac_mode,
                old_temperature,
            )

            # check if old state can be restored
            if (
                old_hvac_mode is None
                or old_hvac_mode not in self.hvac_modes
                or old_preset_mode not in self.preset_modes
                or ATTR_HVAC_DEFINITION not in old_state.attributes
            ):
                raise ValueError(
                    f"Invalid old hvac def '{old_hvac_mode}', start in off mode"
                )

            self._logger.info("restore old controller settings")
            self._hvac_mode_init = old_hvac_mode
            self._preset_mode = old_preset_mode

            # no more data needed for master
            if self.is_master:
                return

            self._self_controlled = old_state.attributes.get(
                ATTR_SELF_CONTROLLED, OperationMode.SELF
            )

            # set to pending state in order to be able again with sync with master
            if self._self_controlled == OperationMode.MASTER:
                self._logger.info("change state to pending master update")
                self._self_controlled = OperationMode.PENDING

            # restore old hvac modes
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

                # restore old temp when within range
                if (
                    old_temperature is not None
                    and min_temp <= old_temperature <= max_temp
                ):
                    self._hvac_def[old_hvac_mode].target_temperature = old_temperature

        except Exception as e:
            self._hvac_mode_init = HVACMode.OFF
            self._logger.warning("restoring old state failed:%s", str(e))
            return

    @property
    def extra_state_attributes(self) -> dict:
        """Attributes to include in entity."""
        tmp_dict = {}
        for key, data in self._hvac_def.items():
            tmp_dict[key] = data.get_variable_attr

        # master attributes
        if self.is_master:
            return {
                CONF_AREA: self._area,
                ATTR_HVAC_DEFINITION: tmp_dict,
                ATTR_EMERGENCY_MODE: self._emergency_stop,
            }
        # for satellite states
        else:
            return {
                ATTR_EMERGENCY_MODE: self._emergency_stop,
                ATTR_SELF_CONTROLLED: self._self_controlled,
                ATTR_CURRENT_TEMP_VEL: self.current_temperature_velocity,
                ATTR_CURRENT_OUTDOOR_TEMPERATURE: self.outdoor_temperature,
                ATTR_FILTER_MODE: self.filter_mode,
                CONF_AREA: self._area,
                ATTR_HVAC_DEFINITION: tmp_dict,
            }

    def set_detailed_output(self, hvac_mode: HVACMode, new_mode: bool) -> None:
        """Configure attribute output level."""
        self._hvac_def[hvac_mode].detailed_output = new_mode
        self.schedule_update_ha_state()

    @callback
    def async_set_pwm_threshold(
        self, hvac_mode: HVACMode, new_threshold: float
    ) -> None:
        """Set new PID Controller min pwm value."""
        self._logger.info(
            "new minimum for pwm scale for '%s' to: '%s'", hvac_mode, new_threshold
        )
        self._hvac_def[hvac_mode].pwm_threshold(new_threshold)
        self.schedule_update_ha_state()

    @callback
    def async_set_pid(
        self,
        hvac_mode: HVACMode,
        kp: float | None = None,
        ki: float | None = None,
        kd: float | None = None,
        update: bool = False,
    ) -> None:  # pylint: disable=invalid-name
        """Set new PID Controller Kp,Ki,Kd value."""
        self._logger.info("new PID for '%s' to: %s;%s;%s", hvac_mode, kp, ki, kd)
        self._hvac_def[hvac_mode].set_pid_param(kp=kp, ki=ki, kd=kd, update=update)
        self.schedule_update_ha_state()

    async def async_set_filter_mode(self, mode: int) -> None:
        """Change filter mode."""
        self.set_filter_mode(mode)
        self.schedule_update_ha_state()

    def set_filter_mode(self, mode: int) -> None:
        """Set new filter for the temp sensor."""
        self._filter_mode = mode
        self._logger.info("modified sensor filter mode to: '%s'", mode)

        # no ukf filter
        if mode == 0:
            self._current_temperature = self.current_temperature
            self._kf_temp = None
            if self._hvac_on:
                self._hvac_on.current_state = None
                self._hvac_on.current_temperature = self.current_temperature
        else:
            cycle_time = 60  # dt is updated when calling predict

            # init ukf when mode from 0 to >0
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

    def get_hvac_data(self, hvac_mode: HVACMode) -> list:
        """Retrieve hvac config and entitiy for hvac mode."""
        found_mode = True
        hvac_on = None
        entity_id = None

        if hvac_mode is None:
            hvac_on = self._hvac_on
            hvac_mode = self._hvac_mode
        elif hvac_mode == HVACMode.OFF:
            pass
        elif hvac_mode not in self.hvac_modes:
            found_mode = False
            # self._logger.error(
            #     "Unrecognized hvac mode when retrieving data: '%s'", hvac_mode
            # )
        elif hvac_mode in [HVACMode.HEAT, HVACMode.COOL]:
            hvac_on = self._hvac_def[hvac_mode]

        if hvac_on:
            entity_id = hvac_on.get_hvac_switch

        return [found_mode, hvac_on, entity_id]

    @callback
    def async_set_integral(self, hvac_mode: HVACMode, integral: float) -> None:
        """Set new PID Controller integral value."""
        self._logger.info("new PID integral for '%s' to: '%s'", hvac_mode, integral)
        self._hvac_def[hvac_mode].set_integral(integral)
        self.schedule_update_ha_state()

    @callback
    def async_set_goal(self, hvac_mode: HVACMode, goal: float) -> None:
        """Set new valve Controller goal value."""
        self._logger.info("new PID valve goal for '%s' to: '%s'", hvac_mode, goal)
        self._hvac_def[hvac_mode].goal = goal
        self.schedule_update_ha_state()

    @callback
    def async_set_ka_kb(
        self, hvac_mode: HVACMode, ka: float | None = None, kb: float | None = None
    ) -> None:  # pylint: disable=invalid-name
        """Set new weather Controller ka,kb value."""
        self._logger.info("new weatehr ka,kb '%s' to: %s;%s", hvac_mode, ka, kb)
        self._hvac_def[hvac_mode].set_ka_kb(ka=ka, kb=kb)
        self.schedule_update_ha_state()

    @callback
    def async_set_satelite_mode(
        self,
        control_mode: OperationMode,
        offset: float | None = None,
        sat_id: int = 0,
        pwm_start_time: float = 0,
        master_delay: float = 0,
    ) -> None:
        """Satellite update from master.

        Originates from master to control satellite routines
        control_mode 'no_change' to only update offset.
        """
        pwm_loop = False
        # mod controller update
        self._logger.info(
            "sat update received for mode:'%s'; offset:'%s'", control_mode, offset
        )

        # no current and no previous thus return
        if self._old_mode == HVACMode.OFF and self._hvac_on is None:
            self._self_controlled = control_mode
            self._pwm_start_time = pwm_start_time
            return

        # if current mode is off: return
        if self._hvac_on is None:
            return

        if not self._hvac_on.is_hvac_proportional_mode:
            self._logger.warning("sat update for non-proportional thermostat")
            return

        # update offset
        if offset is not None:
            if self.control_output[ATTR_CONTROL_OFFSET] != offset:
                self._hvac_on.time_offset = offset
                self.control_output[ATTR_CONTROL_OFFSET] = offset
            pwm_loop = True
        else:
            self._hvac_on.time_offset = 0
            self.control_output[ATTR_CONTROL_OFFSET] = 0
            pwm_loop = True

        # turn thermostat to self controlled
        if (
            control_mode == OperationMode.SELF
            and self._self_controlled != OperationMode.SELF
        ):
            self._logger.debug("sat update to self-controlled state")
            self._self_controlled = OperationMode.SELF
            self._hvac_on.master_delay = 0
            self._sat_id = 0
            # stop and reset current controller
            self._async_routine_controller()
            # cancel scheduled switch routines
            self._async_cancel_pwm_routines()
            # include pwm routine
            self._pwm_start_time = time.time() + CONTROL_START_DELAY

            # start controller loop
            async_track_point_in_utc_time(
                self.hass,
                self.async_routine_controller_factory(
                    self._hvac_on.get_operate_cycle_time
                ),
                datetime.datetime.fromtimestamp(self._pwm_start_time),
            )

            # start pwm loop
            async_track_point_in_utc_time(
                self.hass,
                self.async_routine_pwm_factory(self._hvac_on.get_pwm_time),
                datetime.datetime.fromtimestamp(self._pwm_start_time + PWM_LAG),
            )

        # activate satellite mode
        elif control_mode == OperationMode.MASTER:
            if self._self_controlled in [
                OperationMode.PENDING,
                OperationMode.SELF,
            ]:
                # new hvac mode thus all switches off
                self._pwm_start_time = pwm_start_time
                self._sat_id = sat_id
                self._self_controlled = OperationMode.MASTER
                self._hvac_on.master_delay = master_delay

                # stop controller/pwm routines
                if self._loop_controller:
                    self._logger.debug("sat update: stopping controller routine")
                    self._async_routine_controller()
                if self._loop_pwm:
                    self._logger.debug("sat update: stopping pwm routine")
                    self._async_routine_pwm()

                # cancel scheduled switch routines
                self._async_cancel_pwm_routines()

                # schedule controller loop in sync with master
                async_track_point_in_utc_time(
                    self.hass,
                    self.async_routine_controller_factory(
                        self._hvac_on.get_operate_cycle_time
                    ),
                    datetime.datetime.fromtimestamp(
                        self._pwm_start_time  # master control loop
                        - sat_id * SAT_CONTROL_LEAD  # create some time inbetween sats
                        - MASTER_CONTROL_LEAD  # sat control loop before master
                    ),
                )
                # no pwm loop after master change wait for new offsets
                pwm_loop = False

        # update pwm start-stop rountines
        if pwm_loop:
            self.hass.async_create_task(self._async_controller_pwm(force=True))

    async def async_set_temperature(self, **kwargs) -> None:
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

        # when custom preset mode is active, do not operate
        if self.preset_mode in self._hvac_on.custom_presets:
            self._logger.debug(
                "Preset mode {self.preset_mode} active when temperature is updated : skipping change"
            )
            return

        self._hvac_on.target_temperature = round(temperature, 3)

        # operate in all cases except off
        if self._hvac_mode != HVACMode.OFF:
            await self._async_controller(force=True)

        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change hvac mode."""
        # No changes have been made
        if self._hvac_mode == hvac_mode:
            return

        found_mode, _hvac_on, _ = self.get_hvac_data(hvac_mode)

        if not found_mode:
            return

        self._logger.info("HVAC mode changed to '%s'", hvac_mode)

        # cancel active routines
        if self._hvac_on:
            # cancel scheduled switch routines
            self._async_cancel_pwm_routines(self._hvac_mode)

            # stop controller loop
            self._async_routine_controller()

            # stop pwm loop when present
            if self._loop_pwm:
                self._async_routine_pwm()

            # reset control output
            self.control_output = {ATTR_CONTROL_OFFSET: 0, ATTR_CONTROL_PWM_OUTPUT: 0}

            # stop tracking satelites
            if self.is_master:
                await self._async_routine_track_satelites()
                satelite_reset = {sat: 0 for sat in self._hvac_on.get_satelites}
                self._async_change_satelite_modes(
                    satelite_reset, control_mode=OperationMode.SELF
                )
                # reset to be ready for new satelites
                self._hvac_on.restore_satelites()

        # set current mode
        self._old_mode = self._hvac_mode
        self._hvac_mode = hvac_mode
        self._hvac_on = None

        if self._hvac_mode == HVACMode.OFF:
            self._logger.info(
                "HVAC mode is OFF. Turn the devices OFF and exit hvac change"
            )
            self._self_controlled = OperationMode.SELF
            self.async_write_ha_state()
            return

        # # load current config
        # _hvac_on = self._hvac_def[self._hvac_mode]

        # check and sync preset mode
        if self.preset_mode != _hvac_on.preset_mode:
            await self.async_set_preset_mode(
                self.preset_mode, hvac_mode=self._hvac_mode
            )

        self._hvac_on = _hvac_on

        # reset time stamp pid to avoid integral run-off
        if self._hvac_on.is_prop_pid_mode or self._hvac_on.is_valve_mode:
            self.time_changed = time.time()
            self._hvac_on.pid_reset_time()

        # start listening for outdoor sensors
        if self._hvac_on.is_wc_mode and self.outdoor_temperature is not None:
            self._hvac_on.outdoor_temperature = self.outdoor_temperature

        # set in pending state for satellite mode changed while by master controlled
        if self._self_controlled == OperationMode.MASTER:
            self._logger.info(
                "HVAC change to active state in MASTER state, wait for MASTER. Set on PENDING and wait for master"
            )
            self._self_controlled = OperationMode.PENDING
            self.async_write_ha_state()
            return

        elif self._self_controlled == OperationMode.PENDING:
            self._logger.info(
                "HVAC change in pending state, wait for MASTER. Turn the switch OFF and exit hvac change"
            )
            await self._async_switch_turn_off()
            self.async_write_ha_state()
            return

        # thermostat in hysteris mode
        if self._hvac_on.is_hvac_on_off_mode:
            # no need for pwm routine as controller assures update
            if self._hvac_on.get_operate_cycle_time:
                self._async_routine_controller(self._hvac_on.get_operate_cycle_time)

        # proportional or master start-up
        elif self.is_master or self._hvac_on.is_hvac_proportional_mode:
            self._pwm_start_time = time.time()
            if self.is_master:
                self._pwm_start_time += CONTROL_START_DELAY

            # update and track satelites
            if self.is_master:
                # update controller (reset) to be ready for new satelites
                self._hvac_on.restore_satelites()
                # bring controllers of satelite in sync with master
                # use the pwm
                satelite_reset = {sat: 0 for sat in self._hvac_on.get_satelites}
                self._async_change_satelite_modes(
                    satelite_reset,
                    control_mode=OperationMode.MASTER,
                )

                # start tracking changes of satelites
                await self._async_routine_track_satelites(
                    entity_list=self._hvac_on.get_satelites
                )

            # run controller before pwm loop
            async_track_point_in_utc_time(
                self.hass,
                self.async_routine_controller_factory(
                    self._hvac_on.get_operate_cycle_time
                ),
                datetime.datetime.fromtimestamp(self._pwm_start_time),
            )

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
    def async_routine_controller_factory(self, interval: float | None = None):
        """Generate turn on callbacks as factory."""

        # TODO: factory needed?
        async def async_run_routine(now):
            """Run controller with interval."""
            self._async_routine_controller(interval=interval)

        return async_run_routine

    @callback
    def _async_routine_controller(self, interval: float | None = None) -> None:
        """Run main controller at specified interval."""
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

            # run controller for first time
            self.hass.async_create_task(self._async_controller())

    @callback
    def async_routine_pwm_factory(self, interval: float | None = None):
        """Generate pwm controller callbacks as factory."""

        # TODO: factory needed?
        async def async_run_routine(now):
            """Run pwm controller."""
            self._async_routine_pwm(interval=interval)

        return async_run_routine

    @callback
    def _async_routine_pwm(self, interval: float | None = None) -> None:
        """Run main pwm at specified interval."""
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
            self.hass.async_create_task(self._async_controller_pwm())
            self.async_on_remove(self._loop_pwm)

    async def _async_routine_track_satelites(
        self, entity_list: list | None = None
    ) -> None:
        """Follow changes from satelite thermostats."""
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
    def _async_indoor_temp_change(
        self, event: EventType[EventStateChangedData]
    ) -> None:
        """Handle temperature change.

        Only call emergency stop due to stale sensor, ignore invalid values
        """
        new_state = event.data.get("new_state")
        self._logger.debug("New sensor temperature '%s'", new_state.state)

        if new_state is None or new_state.state in ERROR_STATE:
            self._logger.warning(
                "Sensor temperature %s invalid: %s, skip current state",
                new_state.name,
                new_state.state,
            )
            return
        elif not is_float(new_state.state):
            self._logger.warning(
                "Sensor temperature %s unclear: %s type %s, skip current state",
                new_state.name,
                new_state.state,
                type(new_state.state),
            )
            return
        elif float(new_state.state) < -50 or float(new_state.state) > 50:
            self._logger.warning(
                "Sensor temperature %s unrealistic: %s, skip current state",
                new_state.name,
                new_state.state,
            )
            return
        elif self.preset_mode == PRESET_EMERGENCY:
            self._async_restore_emergency_stop(self._sensor_entity_id)

        self.hass.async_create_task(self._async_update_current_temp(new_state.state))

        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if self._hvac_on is not None and self._hvac_on.is_hvac_on_off_mode:
            self.hass.async_create_task(self._async_controller())

    @callback
    def _async_outdoor_temp_change(
        self, event: EventType[EventStateChangedData]
    ) -> None:
        """Handle outdoor temperature changes.

        Only call emergency stop due to stale sensor, ignore invalid values
        """
        new_state = event.data.get("new_state")
        self._logger.debug("New sensor outdoor temperature '%s'", new_state.state)
        if new_state is None or new_state.state in ERROR_STATE:
            self._logger.debug(
                "Outdoor sensor temperature %s invalid %s, skip current state",
                new_state.name,
                new_state.state,
            )
            return
        elif not is_float(new_state.state):
            self._logger.warning(
                "Outdoor sensor temperature %s unclear: %s type %s, skip current state",
                new_state.name,
                new_state.state,
                type(new_state.state),
            )
            return
        elif self.preset_mode == PRESET_EMERGENCY:
            self._async_restore_emergency_stop(self._sensor_out_entity_id)

        self._async_update_outdoor_temperature(new_state.state)

    @callback
    def _async_stale_sensor_check(self, now: datetime.datetime | None = None) -> None:
        """Check if the sensor has emitted a value during the allowed stale period."""
        entity_list = []
        if self._sensor_entity_id:
            entity_list.append(self._sensor_entity_id)
        if self._sensor_out_entity_id:
            entity_list.append(self._sensor_out_entity_id)

        # check all sensors
        for entity_id in entity_list:
            sensor_state = self.hass.states.get(entity_id)
            if (
                datetime.datetime.now(datetime.UTC) - sensor_state.last_updated
                > self._sensor_stale_duration
            ):
                self._logger.debug(
                    "'%s' last received update is %s, duration is '%s', limit is '%s'",
                    entity_id,
                    sensor_state.last_updated,
                    datetime.datetime.now(datetime.UTC) - sensor_state.last_updated,
                    self._sensor_stale_duration,
                )

                self._async_activate_emergency_stop("stale sensor", sensor=entity_id)

    @callback
    def _async_stuck_switch_check(self, now) -> None:
        """Check if the switch has not changed for a certain period and force operation to avoid stuck or jammed."""

        # operated by master and check if currently active
        if self._self_controlled != OperationMode.SELF:
            master_mode = state_attr(
                # self.hass, "climate." + self._self_controlled, "hvac_action"
                self.hass,
                "climate.master",
                "hvac_action",
            )

            # cancel when master in operation
            if master_mode in [HVACAction.HEATING, HVACAction.COOLING]:
                return

        # check if thermostat is in operation
        if self._hvac_on and self._is_valve_open():
            return

        # get data of all switches
        entity_list = {}
        for hvac_mode, mode_config in self._hvac_def.items():
            if mode_config.get_switch_stale:
                entity_list[hvac_mode] = [
                    mode_config.get_hvac_switch,
                    mode_config.get_switch_stale,
                    mode_config.switch_last_change,
                ]

        if not entity_list:
            self._logger.warning(
                "jamming/stuck prevention activated but no duration set for switches"
            )
            return

        # check each switch
        for hvac_mode, data in entity_list.items():
            # check if switch activated emergency mode
            if data[0] in self._emergency_stop:
                return

            self._logger.debug(
                "Switch '%s' stuck prevention check with last update '%s'",
                data[0],
                data[2],
            )

            # check if too long not operated
            if datetime.datetime.now(datetime.UTC) - data[2] > data[1]:
                self._logger.info(
                    "Switch '%s' stuck prevention activated: not changed state for '%s'",
                    data[0],
                    datetime.datetime.now(datetime.UTC) - data[2],
                )

                # run short operation of switch
                self.hass.async_create_task(
                    self._async_toggle_switch(hvac_mode, data[0])
                )

    @callback
    def _async_satelite_change(self, event: EventType[EventStateChangedData]) -> None:
        """Handle satelite thermostat changes."""
        new_state = event.data.get("new_state")
        if not new_state:
            self._logger.error("Error receiving thermostat update. 'None' received")
            return
        self._logger.debug(
            "Receiving update from '%s'",
            new_state.name,
        )

        # check if stuck loop is triggered
        for hvac_def in new_state.attributes[ATTR_HVAC_DEFINITION].values():
            if hvac_def.get(ATTR_STUCK_LOOP):
                self._logger.debug(
                    "'%s' is in stuck loop, ignore update",
                    new_state.name,
                )
                return

        # check if satellite operating in correct mode
        if new_state.state == self.hvac_mode and new_state.attributes.get(
            ATTR_SELF_CONTROLLED
        ) in [True, OperationMode.PENDING]:
            # force satellite to master mode
            self._async_change_satelite_modes(
                {new_state.name: 0},
                control_mode=OperationMode.MASTER,
            )
            return

        # updating master controller and check if pwm needs update
        update_required = self._hvac_on.update_satelite(new_state)
        if update_required and not self.pwm_controller_time:
            self._logger.debug(
                "Significant update from satelite: '%s' rerun controller",
                new_state.name,
            )
            self.hass.async_create_task(self._async_controller(force=True))

            # if master mode is active: do not call operate but let pwm cycle handle it

            # self.schedule_update_ha_state(force_refresh=False)

    @callback
    def _async_switches_change(self, event: EventType[EventStateChangedData]) -> None:
        """Handle device switch state changes."""
        new_state = event.data.get("new_state")
        entity_id = event.data.get(ATTR_ENTITY_ID)
        self._logger.debug(
            "'%s' switch changed to '%s'",
            entity_id,
            new_state.state,
        )
        # catch multipe options
        if new_state.state in ERROR_STATE:
            self._async_activate_emergency_stop(
                "switch to error state change", sensor=entity_id
            )
        elif new_state.state in NOT_SUPPORTED_SWITCH_STATES:
            self._async_activate_emergency_stop(
                f"not supported switch state {new_state.state}",
                sensor=entity_id,
            )
        # valid switch state
        else:
            if self.preset_mode == PRESET_EMERGENCY:
                self._async_restore_emergency_stop(entity_id)

            if self._hvac_mode in [HVACMode.HEAT, HVACMode.COOL]:
                other_mode = [HVACMode.HEAT, HVACMode.COOL]
                other_mode.remove(self._hvac_mode)
                if (
                    entity_id != self._hvac_on.get_hvac_switch
                    and self._is_valve_open()
                    and self._is_valve_open(hvac_mode=other_mode)
                ):
                    self._logger.warning(
                        "valve of %s is open. Other hvac mode switch '%s' changed to %s, keep in closed state",
                        self._hvac_mode,
                        entity_id,
                        new_state.state,
                    )

                    self.hass.async_create_task(
                        self._async_switch_turn_off(hvac_mode=other_mode)
                    )

            else:
                # not a current active thermostat thus switch state change should not be triggered
                # unless stuck loop prevention is running
                for hvac_mode, data in self._hvac_def.items():
                    if (
                        data.get_hvac_switch == entity_id
                        and not data.stuck_loop
                        and self._is_valve_open(hvac_mode=hvac_mode)
                    ):
                        self._logger.warning(
                            "No switches should be activated in hvac 'off' mode: restore switch '%s' from  %s to 'idle' state",
                            entity_id,
                            new_state.state,
                        )
                        self.hass.async_create_task(
                            self._async_switch_turn_off(hvac_mode=hvac_mode)
                        )

        self.schedule_update_ha_state(force_refresh=False)

    async def _async_update_current_temp(
        self, current_temp: float | None = None
    ) -> None:
        """Update thermostat, optionally with latest state from sensor."""
        if current_temp:
            self._logger.debug("Room temperature updated to '%s'", current_temp)
            # store local in case current hvac mode is off
            self._current_temperature = float(current_temp)

            # setup filter after first temp reading
            if not self._kf_temp and self.filter_mode > 0:
                self.set_filter_mode(self.filter_mode)

        # update ukf filter
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

    @callback
    def _async_update_outdoor_temperature(
        self, current_temp: float | None = None
    ) -> None:
        """Update thermostat with latest state from outdoor sensor."""
        if current_temp:
            self._logger.debug("Outdoor temperature updated to '%s'", current_temp)
            self._outdoor_temperature = float(current_temp)
            if self._hvac_on:
                self._hvac_on.outdoor_temperature = self._outdoor_temperature

    async def _async_update_controller_temp(self) -> None:
        """Update temperature to controller routines."""
        # TODO: async needed?
        if self._hvac_on:
            if not self._kf_temp:
                self._hvac_on.current_temperature = self._current_temperature
            elif not self.is_master:
                self._hvac_on.current_state = [
                    self._kf_temp.get_temp,
                    self._kf_temp.get_vel,
                ]

    @callback
    def _async_change_satelite_modes(
        self, data: dict, control_mode: OperationMode = OperationMode.NO_CHANGE
    ) -> None:
        """Create tasks by master to update all satelites and/or update pwm offset."""

        if data:
            for satelite, offset in data.items():
                # factory as in device_sun_light_trigger
                # +1 to account for master
                if control_mode == OperationMode.MASTER:
                    sat_id = self._hvac_on.get_satelites.index(satelite) + 1
                    delay = self._hvac_on.compensate_valve_lag
                else:
                    sat_id = 0
                    delay = 0

                # create tasks to update
                self.hass.async_create_task(
                    self._async_send_satelite_data(
                        satelite,
                        offset,
                        control_mode=control_mode,
                        sat_id=sat_id,
                        pwm_start_time=self._pwm_start_time,
                        master_delay=delay,
                    )
                )

        else:
            self._logger.debug("No satelite data to send")

    async def _async_send_satelite_data(
        self,
        satelite: str,
        offset: float,
        control_mode: OperationMode = OperationMode.NO_CHANGE,
        sat_id: int = 0,
        pwm_start_time: int = 0,
        master_delay: float = 0,
    ) -> None:
        """Actual sending of control update to a satelite."""
        self._logger.debug(
            "send data to satelite %s %s %s", satelite, offset, control_mode
        )

        # call service to change satellite
        await self.hass.services.async_call(
            "multizone_thermostat",
            "satelite_mode",
            {
                ATTR_ENTITY_ID: "climate." + satelite,
                ATTR_CONTROL_MODE: control_mode,
                ATTR_CONTROL_OFFSET: offset,
                "sat_id": sat_id,
                "pwm_start_time": pwm_start_time,
                "master_delay": master_delay,
            },
            context=self._context,
            # blocking=False,
        )

    async def _async_check_duration(self, routine: bool, force: bool) -> bool:
        """Check if switch change in on-off mode has been long enough.

        on_off is also true when pwm = 0 therefore != _is_pwm_active
        """

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

    def update_pwm_time(self) -> None:
        """Determine if new pwm cycle has started and update cycle time.

        '_pwm_start_time refers' to start of current pwm cycle.
        """
        pwm_duration = self._hvac_on.get_pwm_time.seconds
        # if time.time() > self._pwm_start_time + pwm_duration:
        while time.time() > self._pwm_start_time + pwm_duration:
            self._pwm_start_time += pwm_duration

    @property
    def pwm_controller_time(self) -> bool:
        """Check if pwm loop is to be started soon."""
        next_pwm_loop = self._pwm_start_time
        now = time.time()
        time_diff = next_pwm_loop - now

        if (
            time_diff > 0
            and time_diff / self._hvac_on.get_pwm_time.seconds < CLOSE_TO_PWM
        ):
            self._logger.debug("pwm loop starts soon")
            return True
        else:
            self._logger.debug("no pwm loop to start soon")
            return False

    @callback
    def async_run_controller_factory(self, force: bool = False):
        """Generate controller callbacks as factory."""

        # TODO: factory needed?
        async def async_run_controller(now: datetime.datetime):
            """Run controller."""
            await self._async_controller(force=force)

        return async_run_controller

    async def _async_controller(
        self, now: datetime.datetime | None = None, force: bool = False
    ) -> None:
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            # now is passed by to the callback the async_track_time_interval function , and is set to "now"
            routine = now is not None  # boolean

            self._logger.debug(
                "Controller: calculate output, routine=%s; forced=%s", routine, force
            )

            # do not run when not in sync with master
            if self._self_controlled == OperationMode.PENDING:
                self._logger.debug("Controller cancelled due to 'pending mode'")
                return

            # check emergency mode
            if self.preset_mode == PRESET_EMERGENCY:
                if not self._emergency_stop:
                    self._async_restore_emergency_stop("")
                self._logger.debug("Controller cancelled due to 'emergency mode'")
                return

            # routine should not be called when thermostat is off
            if not self._hvac_on:
                self._logger.warning(
                    "Control update should not be activate when hvac  mode is 'off', exit routine"
                )
                return

            # update and check current temperatures for pwm cycle
            if routine and not self.is_master:
                await self._async_update_current_temp()

            # send temperature to controller
            if not self.is_master:
                await self._async_update_controller_temp()

            # cancel whne no sensor readings are present
            if (
                self._hvac_on.is_hvac_on_off_mode
                or self._hvac_on.is_hvac_proportional_mode
            ):
                if self._sensor_entity_id and self._hvac_on.current_temperature is None:
                    self._logger.warning(
                        "cancel control loop: current temp is None while running controller routine."
                    )
                    return

            # cancel when no outdoor reading
            if self._hvac_on.is_wc_mode:
                if self._sensor_out_entity_id and (
                    self._hvac_on.outdoor_temperature is None
                    or self._hvac_on.target_temperature is None
                ):
                    self._logger.warning(
                        "cancel control loop: current outdoor temp is '%s' and setpoint is '%s' cannot run weather mode",
                        self._hvac_on.outdoor_temperature,
                        self._hvac_on.target_temperature,
                    )
                    return

            # for mode on_off
            if self._hvac_on.is_hvac_on_off_mode:
                if not await self._async_check_duration(routine, force):
                    return

            # determine point in time of current pwm loop
            if self._hvac_on.get_pwm_time.seconds:
                offset = (
                    time.time() - self._pwm_start_time
                ) / self._hvac_on.get_pwm_time.seconds
            else:
                offset = 0

            # calculate actual pwm
            self._hvac_on.calculate(routine=routine, force=force, current_offset=offset)

            # update satellites
            if self.is_master:
                # set offsets at satelites
                satelite_info = self._hvac_on.get_satelite_offset()
                self._async_change_satelite_modes(satelite_info)

            # get controller output
            self.control_output = self._hvac_on.get_control_output
            self._logger.debug(
                "Obtained current control output: '%s'", self.control_output
            )

            # check if pwm loop needs update
            if (
                force  # forced run
                or self._hvac_on.is_hvac_on_off_mode  # hysteris
                or (
                    (self._hvac_on.is_hvac_proportional_mode or self.is_master)
                    and not self._hvac_on.get_pwm_time  # proportional valve
                )
                or (routine and self.is_master)  # master routine cycle
            ):
                self._logger.debug(
                    "Running pwm controller from control loop with 'force=%s'", force
                )
                await self._async_controller_pwm(force=force)

            self.async_write_ha_state()

    async def _async_controller_pwm(
        self, now: datetime.datetime | None = None, force: bool = False
    ) -> None:
        """Convert control output to pwm loop."""
        self._logger.debug(
            "Running pwm routine, routine=%s, forced=%s", now is not None, force
        )

        # keep off in emergency or pwm = 0
        if (
            self.control_output[ATTR_CONTROL_PWM_OUTPUT] in [None, 0]
            or self._hvac_on is None
            or self.preset_mode == PRESET_EMERGENCY
        ):
            self._async_cancel_pwm_routines()
        # determine switch on-off or valve position
        else:
            if self._hvac_on.get_pwm_time:
                pwm_duration = self._hvac_on.get_pwm_time.seconds
            else:
                pwm_duration = None

            # on-off mode switches the pwm between 0 and 100
            if self._hvac_on.is_hvac_on_off_mode:
                if self.control_output[ATTR_CONTROL_PWM_OUTPUT] <= 0:
                    await self._async_switch_turn_off()
                else:
                    await self._async_switch_turn_on()

            # convert pwm to on-off switch
            elif pwm_duration:
                # determine start and end time of valve open
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

                if self._hvac_on.is_hvac_master_mode:
                    start_time += self._hvac_on.compensate_valve_lag

                # stop current schedules
                if self._start_pwm is not None:
                    await self._async_start_pwm()
                if self._stop_pwm is not None:
                    await self._async_stop_pwm()

                if end_time <= start_time:
                    if self._is_valve_open():
                        await self._async_switch_turn_off()
                    return

                # check if current switch state is matching
                if (
                    start_time - now > START_MISALINGMENT or end_time <= now
                ) and self._is_valve_open():
                    await self._async_switch_turn_off()
                elif start_time <= now < end_time:
                    await self._async_switch_turn_on()

                # schedule new switch changes
                if start_time > now:
                    await self._async_start_pwm(start_time)
                if (
                    end_time > now
                    and self.control_output[ATTR_CONTROL_PWM_OUTPUT] != pwm_scale
                ):
                    await self._async_stop_pwm(end_time)

            # convert pwm to proportional switch and close
            else:
                valve_open = self._is_valve_open()

                if (
                    self._hvac_on.pwm_threshold
                    > self.control_output[ATTR_CONTROL_PWM_OUTPUT]
                    and valve_open
                ):
                    await self._async_switch_turn_off()
                # convert pwm to proportional switch and change position
                else:
                    await self._async_switch_turn_on()

    @callback
    def _async_cancel_pwm_routines(self, hvac_mode: HVACMode | None = None) -> None:
        """Cancel scheduled switch routines."""
        if self._async_start_pwm is not None:
            self.hass.async_create_task(self._async_start_pwm())
        if self._async_stop_pwm is not None:
            self.hass.async_create_task(self._async_stop_pwm())

        # if self._hvac_on:
        #     # stop switch
        self.hass.async_create_task(self._async_switch_turn_off(hvac_mode=hvac_mode))

    async def _async_start_pwm(
        self, start_time: datetime.datetime | None = None
    ) -> None:
        """Start pwm at specified time."""
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

    async def _async_stop_pwm(self, stop_time: datetime.datetime | None = None) -> None:
        """Stop pwm at specified time."""
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
    def async_turn_switch_on_factory(
        self, hvac_mode: HVACMode | None = None, control_val: float | None = None
    ):
        """Generate turn on callbacks as factory."""

        # TODO: factory needed?
        async def async_turn_on_switch(now: datetime.datetime):
            """Turn on specific switch."""
            await self._async_switch_turn_on(
                hvac_mode=hvac_mode, control_val=control_val
            )

        return async_turn_on_switch

    def _prop_valve_position(self, hvac_on, control_val: float | None = None):
        """Determine master utilisation for proportional valve scale factor."""
        master_util = 1
        # valve mode
        if not control_val:
            valve_pos = self.control_output[ATTR_CONTROL_PWM_OUTPUT]
        else:
            valve_pos = control_val

        if self._self_controlled == OperationMode.MASTER:
            master_mode = state_attr(
                self.hass, "climate." + self._self_controlled, ATTR_HVAC_DEFINITION
            )
            if self.hvac_mode in master_mode and hvac_on.master_scaled_bound > 1:
                master_control_val = master_mode[self.hvac_mode][ATTR_CONTROL_OUTPUT][
                    ATTR_CONTROL_PWM_OUTPUT
                ]
                master_pwm_scale = master_mode[self.hvac_mode][CONF_PWM_SCALE]
                if master_pwm_scale > 0:
                    master_util = max(
                        1 / hvac_on.master_scaled_bound,
                        master_control_val / master_pwm_scale,
                    )

        # scale valve opening with master pwm
        valve_pos /= master_util
        valve_pos = round(max(0, min(valve_pos, hvac_on.pwm_scale)), 0)

        # NC-NO conversion
        if hvac_on.get_hvac_switch_mode == NO_SWITCH_MODE:
            valve_pos = hvac_on.pwm_scale - valve_pos

        return valve_pos

    async def _async_switch_turn_on(
        self, hvac_mode: HVACMode | None = None, control_val: float | None = None
    ) -> None:
        """Open valve or reposition proportional valve.

        NC/NO aware: NC conversion to NO
        """
        self._logger.debug("Turn ON")
        found_mode, _hvac_on, entity_id = self.get_hvac_data(hvac_mode)

        if not entity_id or not found_mode:
            self._logger.debug("No switch defined for %s", hvac_mode)
            return

        # open valve
        if _hvac_on.is_hvac_switch_on_off:
            if self._is_valve_open(hvac_mode=hvac_mode):
                self._logger.debug("Switch already ON")
                return

            data = {ATTR_ENTITY_ID: entity_id}
            self._logger.debug("Order 'ON' sent to switch device '%s'", entity_id)

            # storetime of operation for stuck switch check
            _hvac_on.switch_last_change = datetime.datetime.now(datetime.UTC)

            # NC-NO conversion
            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                operation = SERVICE_TURN_ON
            else:
                operation = SERVICE_TURN_OFF

            await self.hass.services.async_call(
                HA_DOMAIN, operation, data, context=self._context
            )

        # change valve position
        else:
            valve_pos = self._prop_valve_position(_hvac_on, control_val)
            self._logger.debug(
                "Change state of heater '%s' to '%s'",
                entity_id,
                valve_pos,
            )

            # storetime of operation for stuck switch check
            _hvac_on.switch_last_change = datetime.datetime.now(datetime.UTC)
            data = {
                ATTR_ENTITY_ID: entity_id,
                ATTR_VALUE: valve_pos,
            }
            method = entity_id.split(".")[0]

            await self.hass.services.async_call(
                method,
                SERVICE_SET_VALUE,
                data,
                context=self._context,
            )

    @callback
    def async_turn_switch_off_factory(self, hvac_mode: HVACMode | None = None) -> None:
        """Generate turn on callbacks as factory."""

        async def async_turn_off_switch(now: datetime.datetime):
            """Turn off specific switch."""
            await self._async_switch_turn_off(hvac_mode=hvac_mode)

        return async_turn_off_switch

    async def _async_switch_turn_off(self, hvac_mode: HVACMode | None = None) -> None:
        """Close valve.

        NC/NO aware: NC converted to NO
        """
        self._logger.debug("Turn OFF called")
        found_mode, _hvac_on, entity_id = self.get_hvac_data(hvac_mode)

        if not entity_id or not found_mode:
            self._logger.debug("No switch defined for %s", hvac_mode)
            return

        # operate on-off switch
        if _hvac_on.is_hvac_switch_on_off:
            if not self._is_valve_open(hvac_mode=hvac_mode):
                self._logger.debug("Switch already OFF")
                return

            data = {ATTR_ENTITY_ID: entity_id}
            self._logger.debug("Order 'OFF' sent to switch device '%s'", entity_id)

            # NC-NO conversion
            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                operation = SERVICE_TURN_OFF
            else:
                operation = SERVICE_TURN_ON

            await self.hass.services.async_call(
                HA_DOMAIN, operation, data, context=self._context
            )

        # operate propoertional valve
        else:
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

        _hvac_on.stuck_loop = False

    async def _async_toggle_switch(self, hvac_mode: HVACMode, entity_id: str) -> None:
        """Toggle the state of a switch temporarily and hereafter set it to 0 or 1."""

        _, _hvac_on, _ = self.get_hvac_data(hvac_mode)
        duration = _hvac_on.get_switch_stale_open_time
        _hvac_on.stuck_loop = True

        self._logger.info(
            "switch '%s' toggle state temporarily to ON for %s sec",
            entity_id,
            duration,
        )

        # NO-NC conversion
        if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
            control_val = 0
        else:
            control_val = _hvac_on.pwm_scale

        await self._async_switch_turn_on(hvac_mode=hvac_mode, control_val=control_val)

        # schedule toggle
        async_track_point_in_utc_time(
            self.hass,
            self.async_turn_switch_off_factory(hvac_mode=hvac_mode),
            datetime.datetime.fromtimestamp(time.time() + duration.total_seconds()),
        )

    @callback
    def _async_activate_emergency_stop(self, source: str, sensor: str) -> None:
        """Send an emergency OFF order to HVAC switch."""
        if sensor not in self._emergency_stop:
            self._logger.warning(
                "Emergency OFF order send from %s due to sensor %s", source, sensor
            )
            self._emergency_stop.append(sensor)

            # change to emergency mode in coase not yet activated
            if self.preset_mode != PRESET_EMERGENCY:
                self.hass.async_create_task(
                    self.async_set_preset_mode(PRESET_EMERGENCY)
                )
                # cancel scheduled switch routines
                self._async_cancel_pwm_routines()
        else:
            self._logger.debug("Emergency OFF recall send from %s", source)

    @callback
    def _async_restore_emergency_stop(self, entity_id: str) -> None:
        """Update emergency list."""
        # restore preset when called without any listing
        if not self._emergency_stop:
            self.hass.async_create_task(self.async_set_preset_mode(PRESET_RESTORE))

        elif entity_id in self._emergency_stop:
            self._emergency_stop.remove(entity_id)

            if not self._emergency_stop and self.preset_mode == PRESET_EMERGENCY:
                self._logger.info("Recover from emergency mode")
                self.hass.async_create_task(self.async_set_preset_mode(PRESET_RESTORE))

    async def async_set_preset_mode(
        self, preset_mode: str, hvac_mode: HVACMode | None = None
    ) -> None:
        """Set new preset mode."""
        if (
            preset_mode not in self.valid_presets(hvac_mode)
            and preset_mode != PRESET_RESTORE
        ):
            self._logger.warning(
                "This preset (%s) is not enabled (see the configuration)", preset_mode
            )
            return

        if preset_mode == PRESET_EMERGENCY and not self._emergency_stop:
            self._logger.warning(
                "Preset change '%s' not allowed as no listed errors. REturn to previous mode.",
                preset_mode,
            )
            return

        # already in emergency mode, skip
        if preset_mode == self.preset_mode == PRESET_EMERGENCY:
            return

        if preset_mode != PRESET_RESTORE and self.preset_mode == PRESET_EMERGENCY:
            self._logger.warning(
                "Preset mode change to '%s' not allowed while in emergency mode",
                preset_mode,
            )
            self.async_write_ha_state()
            return

        if self._hvac_on:
            self._logger.debug("Set preset mode to '%s'", preset_mode)
            self._hvac_on.preset_mode = preset_mode

        self._preset_mode = preset_mode

        # sync satellites
        if self._hvac_on:
            if self.is_master:
                await self._async_set_satelite_preset(preset_mode)

            # update thermostat controller when thermostat operating on itself
            elif (
                self.preset_mode != PRESET_EMERGENCY
                and self._self_controlled == OperationMode.SELF
            ):
                await self._async_controller(force=True)

        self.async_write_ha_state()

    async def _async_set_satelite_preset(self, preset_mode: str) -> None:
        """Change preset mode at satelites."""
        for sat in self._hvac_on.get_satelites:
            await self.hass.services.async_call(
                "multizone_thermostat",
                "set_preset_mode",
                {
                    ATTR_ENTITY_ID: "climate." + sat,
                    ATTR_PRESET_MODE: preset_mode,
                    ATTR_HVAC_MODE: self.hvac_mode,
                },
                context=self._context,
                # blocking=False,
            )

    def _is_valve_open(self, hvac_mode: HVACMode | None = None) -> bool:
        """Check if the valve is open.

        NC/NO aware: NO converted to NC
        """
        found_mode, _hvac_on, entity_id = self.get_hvac_data(hvac_mode)

        if not entity_id or not found_mode:
            self._logger.debug("no found entity for %s", hvac_mode)
            return False

        try:
            switch_state = self.hass.states.get(entity_id).state
        except:
            self._async_activate_emergency_stop(
                "valve open check entity not found", sensor=entity_id
            )
            return False

        # check if error state
        if switch_state in ERROR_STATE or (
            not _hvac_on.is_hvac_switch_on_off and not is_float(switch_state)
        ):
            self._async_activate_emergency_stop(
                "active switch state check", sensor=entity_id
            )
            return False

        # restore from error state
        if self.preset_mode == PRESET_EMERGENCY:
            self._async_restore_emergency_stop(entity_id)

        return_val = False
        if _hvac_on.is_hvac_switch_on_off:
            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                if switch_state == STATE_ON:
                    return_val = True
            elif switch_state == STATE_OFF:
                return_val = True
        else:
            if _hvac_on.get_hvac_switch_mode == NC_SWITCH_MODE:
                valve_position = float(switch_state)
            else:
                valve_position = _hvac_on.pwm_scale - float(switch_state)

            if valve_position > 0:
                return_val = True

        return return_val

    @property
    def switch_position(self) -> float | None:
        """Get state of switch.

        NC/NO aware. NO converted to NC
        """
        return_val = None
        entity_id = self._hvac_on.get_hvac_switch

        try:
            sensor_state = self.hass.states.get(entity_id)
            return_val = float(sensor_state.state)
            if self._hvac_on.get_hvac_switch_mode == NO_SWITCH_MODE:
                return_val = self._hvac_on.pwm_scale - return_val
        except:  # noqa: E722
            self._async_activate_emergency_stop(
                "Valve position cannot be read", sensor=entity_id
            )

        return return_val

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return (
            ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.TARGET_TEMPERATURE
        )

    @property
    def precision(self) -> float:
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def target_temperature_step(self) -> float:
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
                if self.preset_mode in self._hvac_on.custom_presets:
                    return self._hvac_on.get_preset_temp
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
                if self.preset_mode in self._hvac_on.custom_presets:
                    return self._hvac_on.get_preset_temp
                elif self._hvac_on.max_target_temp:
                    return self._hvac_on.max_target_temp
        else:
            return None

    @property
    def current_temperature(self) -> float | None:
        """Return the sensor temperature."""
        if self._hvac_on:
            if self.is_master:
                return None

        if not self._kf_temp:
            return self._current_temperature
        else:
            return round(self._kf_temp.get_temp, 3)

    @property
    def current_temperature_velocity(self) -> float | None:
        """Return the sensor temperature velocity."""
        if self._hvac_on:
            if self.is_master:
                return None

        if not self._kf_temp:
            if self._hvac_on:
                if self._hvac_on.is_prop_pid_mode:
                    return self._hvac_on.get_velocity
                else:
                    return "no velocity calculated"
            else:
                return "only available when hvac on"
        else:
            return round(self._kf_temp.get_vel, 5)

    @property
    def outdoor_temperature(self) -> float | None:
        """Return the sensor outdoor temperature."""
        return self._outdoor_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
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
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if self.is_master:
            return 0
        elif (
            self._hvac_mode is HVACMode.OFF
            or self._hvac_mode is None
            or self._hvac_on is None
        ):
            return None
        return self._hvac_on.target_temperature

    @property
    def hvac_modes(self) -> HVACMode:
        """List of available operation modes."""
        return self._enabled_hvac_mode + [HVACMode.OFF]

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode, e.g., home, away, temp."""
        return self._preset_mode

    @property
    def preset_modes(self) -> list:
        """Return a list of available preset modes."""
        return self.valid_presets()

    def valid_presets(self, hvac_mode: HVACMode | None = None):
        """Return a list of available preset modes."""

        _, _hvac_on, _ = self.get_hvac_data(hvac_mode)

        modes = [PRESET_NONE, PRESET_EMERGENCY]
        if _hvac_on is not None:
            if _hvac_on.custom_presets or self.is_master:
                modes = modes + list(_hvac_on.custom_presets.keys())

        return modes

    @property
    def filter_mode(self) -> int:
        """Return the UKF mode."""
        return self._filter_mode

    @filter_mode.setter
    def filter_mode(self, mode: int) -> None:
        """Set the UKF mode."""
        self._filter_mode = mode


def is_float(element) -> bool:
    """Check if input is float."""
    try:
        float(element)
        return True
    except ValueError:
        return False
