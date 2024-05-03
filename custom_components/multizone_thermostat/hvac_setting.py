"""module where configuration of climate is handeled."""
import datetime
import logging
import time

import numpy as np

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, CONF_ENTITY_ID
from homeassistant.core import State
from homeassistant.helpers.typing import ConfigType

from . import DOMAIN, pid_controller, pwm_nesting
from .const import (
    ATTR_CONTROL_MODE,
    ATTR_CONTROL_OFFSET,
    ATTR_CONTROL_OUTPUT,
    ATTR_CONTROL_PWM_OUTPUT,
    ATTR_DETAILED_OUTPUT,
    ATTR_EMERGENCY_MODE,
    ATTR_HVAC_DEFINITION,
    ATTR_KA,
    ATTR_KB,
    ATTR_KD,
    ATTR_KI,
    ATTR_KP,
    ATTR_LAST_SWITCH_CHANGE,
    ATTR_SAT_ALLOWED,
    ATTR_SELF_CONTROLLED,
    ATTR_STUCK_LOOP,
    ATTR_UPDATE_NEEDED,
    CONF_AREA,
    CONF_CONTINUOUS_LOWER_LOAD,
    CONF_CONTROL_REFRESH_INTERVAL,
    CONF_EXTRA_PRESETS,
    CONF_HYSTERESIS_TOLERANCE_OFF,
    CONF_HYSTERESIS_TOLERANCE_ON,
    CONF_INCLUDE_VALVE_LAG,
    CONF_MASTER_MODE,
    CONF_MASTER_OPERATION_MODE,
    CONF_MASTER_SCALE_BOUND,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_VALVE,
    CONF_ON_OFF_MODE,
    CONF_PASSIVE_SWITCH_DURATION,
    CONF_PASSIVE_SWITCH_OPEN_TIME,
    CONF_PID_MODE,
    CONF_PROPORTIONAL_MODE,
    CONF_PWM_DURATION,
    CONF_PWM_RESOLUTION,
    CONF_PWM_SCALE,
    CONF_PWM_SCALE_HIGH,
    CONF_PWM_SCALE_LOW,
    CONF_PWM_THRESHOLD,
    CONF_SATELITES,
    CONF_SENSOR_OUT,
    CONF_SWITCH_MODE,
    CONF_TARGET_TEMP_INIT,
    CONF_TARGET_TEMP_MAX,
    CONF_TARGET_TEMP_MIN,
    CONF_WC_MODE,
    CONF_WINDOW_OPEN_TEMPDROP,
    PID_CONTROLLER,
    PRESET_EMERGENCY,
    PRESET_RESTORE,
    PWM_UPDATE_CHANGE,
    OperationMode,
)


class HVACSetting:
    """Definition and controller for hvac mode."""

    def __init__(
        self,
        name: str,
        hvac_mode: HVACMode,
        conf: ConfigType,
        area: float,
        detailed_output: bool,
    ) -> None:
        """Initialise the configuration of the hvac mode."""
        self._name = name + "." + hvac_mode
        self._logger = logging.getLogger(DOMAIN).getChild(self._name)
        self._logger.debug("Init config for hvac_mode: '%s'", hvac_mode)

        self._hvac_mode = hvac_mode
        self._preset_mode = PRESET_NONE
        self._old_preset = None
        self._hvac_settings = conf
        self._switch_entity = self._hvac_settings[CONF_ENTITY_ID]
        self.area = area
        self.detailed_output = detailed_output
        self._store_integral = False
        self._master_delay = 0

        self._last_change = datetime.datetime.now(datetime.UTC)

        self._control_output = {
            ATTR_CONTROL_OFFSET: 0,
            ATTR_CONTROL_PWM_OUTPUT: 0,
        }

        self._target_temp = None
        self._current_state = None
        self._current_temperature = None
        self._outdoor_temperature = None
        self.restore_temperature = None

        self._pwm_threshold = None

        # satelite mode settings
        self._time_offset = 0

        # storage control modes
        self._on_off = None
        self._proportional = None
        self._master = None

        # storage sub-controllers
        self._pid = None
        self._wc = None

        self._satelites = None
        self.nesting = None

        self._stuck_loop = False

        self._on_off = self._hvac_settings.get(CONF_ON_OFF_MODE)
        self._proportional = self._hvac_settings.get(CONF_PROPORTIONAL_MODE)
        self._master = self._hvac_settings.get(CONF_MASTER_MODE)

        if self.is_hvac_proportional_mode:
            self._wc = self._proportional.get(CONF_WC_MODE)
            self._pid = self._proportional.get(CONF_PID_MODE)

        elif self.is_hvac_master_mode:
            self._operation_mode = self._master.get(CONF_MASTER_OPERATION_MODE)

        self.init_mode()

    def init_mode(self):
        """Init the defined control modes."""
        if self.is_hvac_on_off_mode:
            self._logger.debug("Setup control mode 'on_off'")
            # self._pwm_threshold = 50
            self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 0
        if self.is_hvac_proportional_mode:
            self._logger.debug("Setup control mode 'proportional'")
            self._pwm_threshold = self._proportional[CONF_PWM_THRESHOLD]
            if self.is_prop_pid_mode:
                self.start_pid()
                self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0
            if self.is_wc_mode:
                self._logger.debug("Init 'weather control' settings")
                self._wc[ATTR_CONTROL_PWM_OUTPUT] = 0
        if self.is_hvac_master_mode:
            self._logger.debug("Setup control mode 'master'")
            self._pwm_threshold = self._master[CONF_PWM_THRESHOLD]
            self.start_master(reset=True)

    def calculate(
        self, routine: bool = False, force: bool = False, current_offset: float = 0
    ) -> None:
        """Calculate the current control values for all activated modes."""
        if self.is_hvac_on_off_mode:
            self.run_on_off()

        elif self.is_hvac_proportional_mode:
            if self.is_wc_mode:
                self.run_wc()

            if self.is_prop_pid_mode:
                self.run_pid(force)
                if self._wc and self._pid:
                    # avoid integral run-off when sum is negative
                    pid = self._pid_cntrl.get_PID_parts
                    if (
                        pid["p"] < 0  # too warm
                        and pid["i"] < -self._wc[ATTR_CONTROL_PWM_OUTPUT]
                        and self._wc[ATTR_CONTROL_PWM_OUTPUT]
                        + self._pid[ATTR_CONTROL_PWM_OUTPUT]
                        < 0
                    ):
                        self.set_integral(-self._wc[ATTR_CONTROL_PWM_OUTPUT])

        elif self.is_hvac_master_mode:
            # nesting of pwm controlled valves
            start_time = time.time()
            if routine:
                self.nesting.nest_rooms(self._satelites)
                self.nesting.distribute_nesting()
                forced_nest = True
            # update nesting length only to avoid too large shifts
            else:
                self.nesting.check_pwm(self._satelites, dt=current_offset)
                forced_nest = False
            # TODO check offsets when thermostat setpoint is raised
            #  - check offset (input val offset)
            #  - excl other rooms

            new_offsets = self.nesting.get_nesting()
            if new_offsets:
                self.set_satelite_offset(new_offsets, forced=forced_nest)

            self._logger.debug(
                "Control calculation dt %.4f sec", time.time() - start_time
            )

    def start_master(self, reset: bool = False) -> None:
        """Init the master mode."""
        if reset:
            self._satelites = {}

        self.nesting = pwm_nesting.Nesting(
            self._name,
            operation_mode=self._operation_mode,
            master_pwm=self.pwm_scale,
            tot_area=self.area,
            min_load=self.get_min_load,
            pwm_threshold=self.pwm_threshold,
            min_prop_valve_opening=self.get_min_valve_opening,
        )

    def start_pid(self) -> None:
        """Init the PID controller."""
        self._logger.debug("Init pid settings")
        lower_pwm_scale, upper_pwm_scale = self.pwm_scale_limits(self._pid)

        kp, ki, kd = self.get_pid_param(self._pid)  # pylint: disable=invalid-name

        self._pid_cntrl = pid_controller.PIDController(
            self._name,
            CONF_PID_MODE,
            self.get_operate_cycle_time.seconds,
            kp,
            ki,
            kd,
            time.time,
            lower_pwm_scale,
            upper_pwm_scale,
        )

        self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0

    def run_on_off(self) -> None:
        """Determine switch state for hvac on_off."""
        tolerance_on, tolerance_off = self.get_hysteris
        target_temp = self.target_temperature
        current_temp = self.current_temperature

        self._logger.debug(
            "on-off - target %s, on %s, off %s, current %.2f",
            target_temp,
            tolerance_on,
            tolerance_off,
            current_temp,
        )

        if self._hvac_mode == HVACMode.HEAT:
            target_temp_min = target_temp - tolerance_on
            target_temp_max = target_temp + tolerance_off

            if current_temp >= target_temp_max:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 0
            elif current_temp <= target_temp_min:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 100

        elif self._hvac_mode == HVACMode.COOL:
            target_temp_min = target_temp - tolerance_off
            target_temp_max = target_temp + tolerance_on

            if current_temp <= target_temp_min:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 0
            elif current_temp >= target_temp_max:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 100

    def run_wc(self) -> None:
        """Calcuate weather compension mode."""
        KA, KB = self.get_ka_kb_param  # pylint: disable=invalid-name
        lower_pwm_scale, upper_pwm_scale = self.pwm_scale_limits(self._wc)

        if self.outdoor_temperature is not None:
            temp_diff = self.target_temperature - self.outdoor_temperature
            self._wc[ATTR_CONTROL_PWM_OUTPUT] = min(
                max(lower_pwm_scale, temp_diff * KA + KB), upper_pwm_scale
            )
            self._logger.debug(
                "weather control contribution %.2f", self._wc[ATTR_CONTROL_PWM_OUTPUT]
            )

        else:
            self._logger.warning("no outdoor temperature; continue with previous data")

    def run_pid(self, force: bool = False) -> None:
        """Calcuate the PID for current timestep."""
        # proportional pid mode
        if isinstance(self.current_state, (list, tuple, np.ndarray)):
            current = self.current_state
            # stop when room cools down too fast
            if self.check_window_open(current[1]):
                # keep current control_output
                return
        else:
            current = self.current_temperature
        setpoint = self.target_temperature

        if self.is_hvac_master_mode and current == 0:
            self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0
        else:
            self._pid[ATTR_CONTROL_PWM_OUTPUT] = self._pid_cntrl.calc(
                current, setpoint, force=force
            )

    def get_control_master(self) -> float:
        """Master pwm based on nesting of satelites for pwm controlled on-off valves."""
        return self.nesting.get_master_output()

    def calc_control_output(self) -> dict:
        """Return the control output (offset and valve pos) of the thermostat."""
        if self.time_offset is None:
            self.time_offset = 0

        if self.is_hvac_on_off_mode:
            control_output = self._on_off[ATTR_CONTROL_PWM_OUTPUT]

        elif self.is_hvac_proportional_mode:
            control_output = 0
            if self.is_prop_pid_mode:
                control_output += self._pid[ATTR_CONTROL_PWM_OUTPUT]
            if self.is_wc_mode:
                control_output += self._wc[ATTR_CONTROL_PWM_OUTPUT]

        elif self.is_hvac_master_mode:
            # Determine valve opening for master valve based on satelites running in
            # proportional hvac mode
            master_output = self.get_control_master()
            self.time_offset = master_output[ATTR_CONTROL_OFFSET]
            control_output = master_output[ATTR_CONTROL_PWM_OUTPUT]

        if self.is_hvac_master_mode or self.is_hvac_proportional_mode:
            if control_output > self.pwm_scale:
                control_output = self.pwm_scale
            # only open above threshold or 0 (or when not set)
            elif control_output < self.pwm_threshold:
                control_output = 0

            elif not self.is_hvac_master_mode and self.get_pwm_time.seconds:
                control_output += (
                    self.master_delay / self.get_pwm_time.seconds * self.pwm_scale
                )

            self._logger.debug("control output before rounding %s", control_output)
            control_output = get_rounded(
                control_output, self.pwm_scale / self.pwm_resolution
            )

        if self.is_hvac_master_mode:
            if self.time_offset + control_output > self.pwm_scale:
                self.time_offset = max(
                    0, self.pwm_scale - (self.time_offset + control_output)
                )

        self._control_output = {
            ATTR_CONTROL_OFFSET: round(self.time_offset, 3),
            ATTR_CONTROL_PWM_OUTPUT: round(control_output, 3),
        }

    @property
    def get_control_output(self) -> dict:
        """Return the control output (offset and valve pos) of the thermostat."""
        return self._control_output

    @property
    def time_offset(self) -> float:
        """Get minimum pwm range."""
        return self._time_offset

    @time_offset.setter
    def time_offset(self, offset: float) -> None:
        """Set time offset pwm start."""
        if self.is_hvac_proportional_mode or self.is_hvac_master_mode:
            self._time_offset = offset

    @property
    def min_target_temp(self) -> float | None:
        """Return minimum target temperature."""
        if self.is_hvac_master_mode:
            return None
        else:
            return self._hvac_settings[CONF_TARGET_TEMP_MIN]

    @property
    def max_target_temp(self) -> float | None:
        """Return maximum target temperature."""
        if self.is_hvac_master_mode:
            return None
        else:
            return self._hvac_settings[CONF_TARGET_TEMP_MAX]

    @property
    def get_target_temp_limits(self) -> list:
        """Get range of allowed setpoint range."""
        return [
            self.min_target_temp,
            self.max_target_temp,
        ]

    @property
    def target_temperature(self) -> float:
        """Return target temperature."""
        # initial request
        if self._target_temp is None and not self.is_hvac_master_mode:
            self._target_temp = self._hvac_settings[CONF_TARGET_TEMP_INIT]
        return self._target_temp

    @target_temperature.setter
    def target_temperature(self, target_temp: float) -> None:
        """Set new target temperature."""
        self._target_temp = target_temp

    @property
    def get_preset_temp(self) -> float | None:
        """Return preset temp for current custom preset."""
        if self.is_hvac_on_off_mode or self.is_hvac_proportional_mode:
            return self.custom_presets[self.preset_mode]
        else:
            return None

    @property
    def custom_presets(self) -> dict:
        """Get allowed preset modes."""
        custom = self._hvac_settings.get(CONF_EXTRA_PRESETS, {})
        return custom

    @property
    def preset_mode(self) -> str:
        """Get preset mode."""
        return self._preset_mode

    @preset_mode.setter
    def preset_mode(self, mode: str) -> None:
        """Set preset mode."""
        if mode == PRESET_EMERGENCY:
            self._old_preset = self.preset_mode
        elif mode == PRESET_RESTORE:
            mode = self._old_preset

        if not self.is_hvac_master_mode and mode not in [
            PRESET_EMERGENCY,
            PRESET_RESTORE,
        ]:
            # switch to custom preset and save old set point
            if self._preset_mode == PRESET_NONE and mode in self.custom_presets:
                self.restore_temperature = self.target_temperature
                self.target_temperature = self.custom_presets[mode]
            # restore from custom preset and restore old set point
            elif self._preset_mode in self.custom_presets and mode == PRESET_NONE:
                if self.restore_temperature is not None:
                    self.target_temperature = self.restore_temperature
                else:
                    self.target_temperature = self._hvac_settings[CONF_TARGET_TEMP_INIT]
            # change between custom presets
            elif (
                self._preset_mode in self.custom_presets and mode in self.custom_presets
            ):
                self.target_temperature = self.custom_presets[mode]
        self._preset_mode = mode

    @property
    def get_hvac_switch(self) -> str:
        """Return the switch entity."""
        return self._switch_entity

    @property
    def is_hvac_switch_on_off(self) -> bool:
        """Check if on-off mode is active."""
        if (
            self.is_hvac_on_off_mode or self.get_pwm_time.seconds != 0
        ):  # change pwm to on_off or prop
            return True

        return False

    @property
    def master_scaled_bound(self) -> float:
        """Check if proporitional valve scales with master pwm."""
        if self.is_hvac_proportional_mode:
            return self._proportional.get(CONF_MASTER_SCALE_BOUND)

        return 1

    @property
    def compensate_valve_lag(self):
        """Delay master valve opening."""
        if self.is_hvac_master_mode:
            return self._master[CONF_INCLUDE_VALVE_LAG].seconds
        return 0

    @property
    def master_delay(self):
        """Master valve delay."""
        return self._master_delay

    @master_delay.setter
    def master_delay(self, delay_time):
        """Master valve delay.

        The delay is added to the control output when offset = 0
        """
        self._master_delay = delay_time

    @property
    def get_hvac_switch_mode(self) -> str:
        """Return the switch entity."""
        return self._hvac_settings[CONF_SWITCH_MODE]

    @property
    def get_switch_stale(self) -> float | None:
        """Return the switch max passive duration."""
        return self._hvac_settings.get(CONF_PASSIVE_SWITCH_DURATION)

    @property
    def get_switch_stale_open_time(self) -> datetime.datetime:
        """Return the switch max passive duration."""
        return self._hvac_settings.get(CONF_PASSIVE_SWITCH_OPEN_TIME)

    @property
    def stuck_loop(self) -> bool:
        """Return if stuck loop is active."""
        return self._stuck_loop

    @stuck_loop.setter
    def stuck_loop(self, val: bool) -> None:
        """Set state stuck loop."""
        self._stuck_loop = val

    @property
    def switch_last_change(self) -> datetime.datetime:
        """Return last time valve opened for stale check."""
        return self._last_change

    @switch_last_change.setter
    def switch_last_change(self, val: datetime.datetime) -> None:
        """Store last time valve opened for stale check."""
        self._last_change = val

    @property
    def get_pwm_time(self) -> datetime.datetime:
        """Return pwm interval time."""
        return self.active_control_data.get(
            CONF_PWM_DURATION, datetime.timedelta(seconds=0)
        )

    @property
    def pwm_resolution(self) -> float:
        """Pwm resolution."""
        return self.active_control_data[CONF_PWM_RESOLUTION]

    @property
    def pwm_scale(self) -> float:
        """Get deadband range."""
        return self.active_control_data.get(CONF_PWM_SCALE)

    def pwm_scale_limits(self, hvac_data: dict) -> list:
        """Bandwidth for control value."""
        upper_pwm_scale = hvac_data.get(
            CONF_PWM_SCALE_HIGH, self.pwm_scale
        )  # else max = pwm scale
        # lower scale is depended on hvac mode
        if CONF_PWM_SCALE_LOW in hvac_data:
            lower_pwm_scale = hvac_data[CONF_PWM_SCALE_LOW]
        elif self.is_prop_pid_mode and self.is_wc_mode:
            # allow to to negative pwm to compensate
            # - prop mode: compensate wc mode
            lower_pwm_scale = -1 * upper_pwm_scale
        else:
            # pwm on-off thermostat dont allow below zero
            lower_pwm_scale = 0

        return [lower_pwm_scale, upper_pwm_scale]

    @property
    def pwm_threshold(self) -> float:
        """Get minimum active_control_data pwm range."""
        return self._pwm_threshold

    def set_pwm_threshold(self, new_threshold: float) -> None:
        """Set minimum pwm."""
        if self.is_hvac_on_off_mode:
            raise ValueError("min diff cannot be set for on-off controller")
        self._pwm_threshold = new_threshold
        if self.is_hvac_master_mode:
            self.start_master()

    def close_to_routine(self, offset):
        """Check if offset is close to routine or when there is not enough time to open."""
        close_to = True
        if offset < 1:
            time_left = (1 - offset) * self.get_pwm_time
            threshold = self.pwm_threshold / self.pwm_scale * self.get_pwm_time
            if time_left - self.compensate_valve_lag > threshold:
                close_to = False

        return close_to

    @property
    def get_operate_cycle_time(self) -> datetime.datetime:
        """Return interval for recalculate (control value)."""
        return self.active_control_data.get(
            CONF_CONTROL_REFRESH_INTERVAL, datetime.timedelta(seconds=0)
        )

    @property
    def get_min_on_off_cycle(self) -> datetime.datetime:
        """Minimum duration before recalcute."""
        if self.is_hvac_on_off_mode:
            return self._on_off.get(
                CONF_MIN_CYCLE_DURATION, datetime.timedelta(seconds=0)
            )

    @property
    def get_hysteris(self) -> list:
        """Get bandwidth for on-off mode."""
        tolerance_on = self._on_off[CONF_HYSTERESIS_TOLERANCE_ON]
        tolerance_off = self._on_off[CONF_HYSTERESIS_TOLERANCE_OFF]

        return [tolerance_on, tolerance_off]

    @property
    def current_state(self) -> list | None:
        """Return current temperature and optionally velocity."""
        return self._current_state

    @current_state.setter
    def current_state(self, state: list) -> None:
        """Set current temperature and optionally velocity."""
        self._current_state = state
        if self._current_state:
            self.current_temperature = state[0]

    @property
    def detailed_output(self) -> bool:
        """Get state detailed output."""
        return self._detailed_output

    @detailed_output.setter
    def detailed_output(self, new_mode: bool) -> None:
        """Change detailed output from service."""
        self._detailed_output = new_mode

    @property
    def current_temperature(self) -> float | None:
        """Set new current temperature."""
        return self._current_temperature

    @current_temperature.setter
    def current_temperature(self, current_temp: float | None) -> None:
        """Set new current temperature."""
        self._current_temperature = current_temp

    @property
    def outdoor_temperature(self) -> float | None:
        """Set new outdoor temperature."""
        return self._outdoor_temperature

    @outdoor_temperature.setter
    def outdoor_temperature(self, current_temp: float | None) -> None:
        """Set new outdoor temperature."""
        self._outdoor_temperature = current_temp

    def check_window_open(self, current: float) -> bool:
        """Check if temp drop is high enough."""
        if CONF_WINDOW_OPEN_TEMPDROP in self._pid:
            window_threshold = (
                self._pid[CONF_WINDOW_OPEN_TEMPDROP] / 3600
            )  # scale to sec
        else:
            return False

        if self._hvac_mode == HVACMode.HEAT:
            if current < window_threshold:
                self._logger.debug(
                    "temperature drop %.5f: open window detected, maintain old control value",
                    current,
                )
                return True
        elif self._hvac_mode == HVACMode.COOL:
            if current > window_threshold:
                self._logger.debug(
                    "temperature rise %.5f: open window detected, maintain old control value",
                    current,
                )
                return True

    def get_pid_param(self, hvac_data: dict) -> list:
        """Return the pid parameters of the thermostat."""
        return (hvac_data.get(ATTR_KP), hvac_data.get(ATTR_KI), hvac_data.get(ATTR_KD))

    def set_pid_param(
        self,
        kp: float | None = None,
        ki: float | None = None,
        kd: float | None = None,
        update: bool = False,
    ) -> None:  # pylint: disable=invalid-name
        """Set PID parameters."""
        if kp is not None:
            self._pid[ATTR_KP] = kp
        if ki is not None:
            self._pid[ATTR_KI] = ki
        if kd is not None:
            self._pid[ATTR_KD] = kd

        if update:
            self._pid_cntrl.set_pid_param(kp=kp, ki=ki, kd=kd)

    def pid_reset_time(self) -> None:
        """Reset the current time for PID to avoid overflow of the intergral part when switching between hvac modes."""
        self._pid_cntrl.reset_time()

    def set_integral(self, integral: float) -> None:
        """Overwrite integral value."""
        self._pid_cntrl.integral = integral

    @property
    def get_integral(self) -> float:
        """Get pid integral value."""
        return self._pid_cntrl.integral

    @property
    def get_velocity(self) -> float:
        """Get pid velocity value."""
        return self._pid_cntrl.differential

    @property
    def get_ka_kb_param(self) -> list:
        """Return the wc parameters of the thermostat."""
        if self.is_wc_mode:
            ka = self._wc[ATTR_KA]  # pylint: disable=invalid-name
            kb = self._wc[ATTR_KB]  # pylint: disable=invalid-name
            return (ka, kb)
        else:
            return (None, None)

    @property
    def get_min_valve_opening(self) -> float:
        """Initial minimum opening of master when prop valves are present.

        Including master delay.
        """
        delay_in_pwm = 0
        if self.get_pwm_time.total_seconds() > 0 and self.compensate_valve_lag > 0:
            delay_in_pwm = self.compensate_valve_lag / self.get_pwm_time.total_seconds()
        return self._master[CONF_MIN_VALVE] + delay_in_pwm

    @property
    def get_wc_sensor(self) -> str | None:
        """Return the sensor entity."""
        if self.is_wc_mode:
            return self._wc[CONF_SENSOR_OUT]
        else:
            return None

    def set_ka_kb(self, ka: float | None = None, kb: float | None = None) -> None:  # pylint: disable=invalid-name
        """Set weather mode parameters."""
        if ka is not None:
            self._wc[ATTR_KA] = ka
        if kb is not None:
            self._wc[ATTR_KB] = kb

    @property
    def get_min_load(self) -> float:
        """Master continuous mode factor.

        Used to set lower bound for heat/cool magnitude
        nesting in continuous mode will use this minimum (as heating area)
        to calculate pwm time duration in low demand conditions eq not
        enough request for continuous operation
        """
        return self._master[CONF_CONTINUOUS_LOWER_LOAD]

    @property
    def get_satelites(self) -> dict | None:
        """Return the satelite thermostats."""
        if self.is_hvac_master_mode:
            return self._master[CONF_SATELITES]
        else:
            return None

    def update_satelite(self, state: State) -> bool:
        """Set and check new state of satelite."""
        sat_name = state.name
        area = state.attributes.get(CONF_AREA)
        self_controlled = state.attributes.get(ATTR_SELF_CONTROLLED)
        update = False

        if state.state != self._hvac_mode:
            self._satelites.pop(sat_name, None)
            update = True
        else:
            preset = state.attributes.get(ATTR_HVAC_DEFINITION)[state.state][
                ATTR_PRESET_MODE
            ]
            control_mode = state.attributes.get(ATTR_HVAC_DEFINITION)[state.state][
                ATTR_CONTROL_MODE
            ]

            if (
                preset == PRESET_EMERGENCY
                or self_controlled != OperationMode.MASTER
                or control_mode != CONF_PROPORTIONAL_MODE
            ):
                self._satelites.pop(sat_name, None)
                update = True

            else:
                self._logger.debug("Save update from '%s'", state)
                pwm_time = state.attributes.get(ATTR_HVAC_DEFINITION)[state.state][
                    CONF_PWM_DURATION
                ]
                pwm_scale = state.attributes.get(ATTR_HVAC_DEFINITION)[state.state][
                    CONF_PWM_SCALE
                ]
                setpoint = state.attributes[ATTR_TEMPERATURE]
                time_offset, control_value = state.attributes.get(ATTR_HVAC_DEFINITION)[
                    state.state
                ][ATTR_CONTROL_OUTPUT].values()

                # check if controller update is needed
                if sat_name in self._satelites:
                    old_val = self._satelites[sat_name][ATTR_CONTROL_PWM_OUTPUT]
                    if old_val == 0:
                        if control_value != 0:
                            update = True
                    elif abs((control_value - old_val) / old_val) > PWM_UPDATE_CHANGE:
                        update = True

                    if setpoint != self._satelites[sat_name][ATTR_TEMPERATURE]:
                        update = True

                    if self._satelites[sat_name][ATTR_UPDATE_NEEDED]:
                        update = True

                elif control_value > 0:
                    update = True

                self._satelites[sat_name] = {
                    ATTR_HVAC_MODE: state.state,
                    ATTR_SELF_CONTROLLED: self_controlled,
                    ATTR_EMERGENCY_MODE: preset,
                    ATTR_CONTROL_MODE: control_mode,
                    CONF_PWM_DURATION: pwm_time,
                    CONF_PWM_SCALE: pwm_scale,
                    ATTR_TEMPERATURE: setpoint,
                    CONF_AREA: area,
                    ATTR_CONTROL_PWM_OUTPUT: control_value,
                    ATTR_CONTROL_OFFSET: time_offset,
                    ATTR_UPDATE_NEEDED: update,
                }

        self._logger.debug("Satellite data requires controller update: %s", update)
        return update

    @property
    def is_satelite_allowed(self) -> bool:
        """Return if satelite mode is allowed.

        on-off cannot be used with master
        """
        if self.is_hvac_proportional_mode and not self.is_hvac_master_mode:
            return True
        else:
            return False

    def get_satelite_offset(self) -> dict:
        """PWM offsets for satelites."""
        self._logger.debug("get sat offsets")
        tmp_dict = {}
        for room, data in self._satelites.items():
            if data[ATTR_UPDATE_NEEDED] is True:
                # only reset update for on-off valves
                # such that prop valves keep scaling to new master pwm
                data[ATTR_UPDATE_NEEDED] = False
                tmp_dict[room] = data[ATTR_CONTROL_OFFSET]
        return tmp_dict

    def restore_satelites(self) -> None:
        """Remove the satelites and nesting."""
        self._satelites = {}
        self.nesting.satelite_data(self._satelites)

    def set_satelite_offset(self, new_offsets: dict, forced: bool = True) -> None:
        """Store offset per satelite."""
        for room, offset in new_offsets.items():
            if room in self._satelites:
                # if self._satelites[room][ATTR_CONTROL_OFFSET] != offset or forced_update:
                if forced or self._satelites[room][ATTR_CONTROL_OFFSET] != offset:
                    self._satelites[room][ATTR_UPDATE_NEEDED] = True
                self._satelites[room][ATTR_CONTROL_OFFSET] = offset

    @property
    def get_control_mode(self) -> str:
        """Active control mode."""
        if self.is_hvac_on_off_mode:
            return CONF_ON_OFF_MODE
        elif self.is_hvac_proportional_mode:
            return CONF_PROPORTIONAL_MODE
        elif self.is_hvac_master_mode:
            return CONF_MASTER_MODE

    @property
    def active_control_data(self) -> ConfigType:
        """Get controller data."""
        if self.is_hvac_on_off_mode:
            return self._on_off
        elif self.is_hvac_proportional_mode:
            return self._proportional
        elif self.is_hvac_master_mode:
            return self._master

    @property
    def is_hvac_on_off_mode(self) -> bool:
        """Check if on-off mode."""
        if self._on_off:
            return True
        else:
            return False

    @property
    def is_hvac_proportional_mode(self) -> bool:
        """Check if proportional mode."""
        if self._proportional:
            return True
        else:
            return False

    @property
    def is_hvac_master_mode(self) -> bool:
        """Check if master mode."""
        if self._master:
            return True
        else:
            return False

    @property
    def is_prop_pid_mode(self) -> bool:
        """Return the control mode."""
        if self._pid:
            return True
        else:
            return False

    @property
    def is_wc_mode(self) -> bool:
        """Check if weather control mode."""
        if self._wc:
            return True
        else:
            return False

    @property
    def get_variable_attr(self) -> ConfigType:
        """Return attributes for climate entity."""
        open_window = None
        if (
            isinstance(self.current_state, (list, tuple, np.ndarray))
            and self.is_hvac_proportional_mode
        ):
            current = self.current_state
            open_window = self.check_window_open(current[1])
        tmp_dict = {}
        tmp_dict[ATTR_PRESET_MODE] = self.preset_mode
        tmp_dict[ATTR_TEMPERATURE] = self.target_temperature
        tmp_dict[ATTR_SAT_ALLOWED] = self.is_satelite_allowed
        tmp_dict[ATTR_CONTROL_MODE] = self.get_control_mode
        tmp_dict[CONF_CONTROL_REFRESH_INTERVAL] = self.get_operate_cycle_time.seconds
        tmp_dict[CONF_PWM_DURATION] = self.get_pwm_time.seconds
        tmp_dict[CONF_PWM_SCALE] = self.pwm_scale
        tmp_dict[ATTR_CONTROL_OUTPUT] = self.get_control_output
        tmp_dict[ATTR_DETAILED_OUTPUT] = self.detailed_output
        tmp_dict[ATTR_LAST_SWITCH_CHANGE] = self.switch_last_change
        tmp_dict[ATTR_STUCK_LOOP] = self.stuck_loop
        tmp_dict["Open_window"] = open_window

        if self.is_hvac_master_mode:
            tmp_dict[CONF_SATELITES] = self.get_satelites
            tmp_dict[CONF_MASTER_OPERATION_MODE] = self._operation_mode

        if self.is_hvac_proportional_mode:
            if self.is_prop_pid_mode:
                tmp_dict["PID_values"] = self.get_pid_param(self._pid)
                if self.is_prop_pid_mode:
                    PID_parts = self._pid_cntrl.get_PID_parts
                    if self.detailed_output:
                        tmp_dict["PID_P"] = round(PID_parts["p"], 3)
                        tmp_dict["PID_I"] = round(PID_parts["i"], 3)
                        tmp_dict["PID_D"] = round(PID_parts["d"], 3)
                        tmp_dict["PID_valve_pos"] = round(
                            self._pid[ATTR_CONTROL_PWM_OUTPUT], 3
                        )
                    elif self._store_integral:
                        tmp_dict["PID_P"] = None
                        tmp_dict["PID_I"] = round(PID_parts["i"], 3)
                        tmp_dict["PID_D"] = None
                        tmp_dict["PID_valve_pos"] = None

            if self.is_wc_mode:
                tmp_dict["ab_values"] = self.get_ka_kb_param
                if self.detailed_output:
                    tmp_dict["wc_valve_pos"] = round(
                        self._wc[ATTR_CONTROL_PWM_OUTPUT], 3
                    )
                else:
                    tmp_dict["wc_valve_pos"] = None
        return tmp_dict

    def restore_reboot(
        self, data: ConfigType, restore_parameters: bool, restore_integral: bool
    ) -> None:
        """Restore attributes for climate entity."""
        self._store_integral = restore_integral
        self.target_temperature = data[ATTR_TEMPERATURE]
        self.switch_last_change = datetime.datetime.strptime(
            data[ATTR_LAST_SWITCH_CHANGE], "%Y-%m-%dT%H:%M:%S.%f%z"
        )

        if self.is_prop_pid_mode:
            if restore_parameters and "PID_values" in data:
                kp, ki, kd = data["PID_values"]  # pylint: disable=invalid-name
                self.set_pid_param(kp=kp, ki=ki, kd=kd, update=True)

        if restore_integral:
            if self.is_prop_pid_mode:
                if "PID_integral" in data:
                    self.set_integral(data["PID_integral"])

        if self._pid:
            self.pid_reset_time()


def get_rounded(input_val: float, min_clip: float) -> float:
    """Round float to min_clip.

    https://stackoverflow.com/questions/7859147/round-in-numpy-to-nearest-step
    """
    scaled = input_val / min_clip
    return np.where(scaled % 1 >= 0.5, np.ceil(scaled), np.floor(scaled)) * min_clip
