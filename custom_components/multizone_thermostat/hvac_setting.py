"""module where configuration of climate is handeled"""
from datetime import timedelta
import logging
import time

import numpy as np

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    PRESET_AWAY,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, CONF_ENTITY_ID

from . import DOMAIN, pid_controller, pwm_nesting
from .const import *


class HVACSetting:
    """definition of hvac mode"""

    def __init__(self, name, hvac_mode, conf, area, detailed_output):
        # self._logger = logging.getLogger(log_id).getChild(hvac_mode)
        self._name = name + "." + hvac_mode
        self._logger = logging.getLogger(DOMAIN).getChild(self._name)
        self._logger.info("Config hvac settings for hvac_mode : '%s'", hvac_mode)

        self._hvac_mode = hvac_mode
        self._preset_mode = PRESET_NONE
        self._old_preset = None
        self._hvac_settings = conf
        self._switch_entity = self._hvac_settings[CONF_ENTITY_ID]
        self.area = area
        self._detailed_output = detailed_output
        self._store_integral = False

        self._target_temp = None
        self._current_state = None
        self._current_temperature = None
        self._outdoor_temperature = None
        self.restore_temperature = None

        self._pwm_threshold = None

        # satelite mode settings
        # self._master_control_interval = None
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
            self._pid = self._master.get(CONF_VALVE_MODE)

        self.init_mode()

    def init_mode(self):
        """init the defined control modes"""
        if self.is_hvac_on_off_mode:
            self._logger.debug("HVAC mode 'on_off' active")
            self._pwm_threshold = 50
            self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 0
        if self.is_hvac_proportional_mode:
            self._logger.debug("HVAC mode 'proportional' active")
            self._pwm_threshold = self._proportional[CONF_PWM_THRESHOLD]
            if self.is_prop_pid_mode:
                self._logger.debug("HVAC mode 'pid' active")
                self.start_pid()
                self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0
            if self.is_wc_mode:
                self._logger.debug("HVAC mode 'weather control' active")
                self._wc[ATTR_CONTROL_PWM_OUTPUT] = 0
        if self.is_hvac_master_mode:
            self._logger.debug("HVAC mode 'master' active")
            self._pwm_threshold = self._master[CONF_PWM_THRESHOLD]
            self.start_master()
            if self.is_valve_mode:
                self._logger.debug("HVAC mode 'valve control' active")
                self.start_pid()
                self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0

    def calculate(self, routine=False, force=False, current_offset=0):
        """Calculate the current control values for all activated modes"""
        if self.is_hvac_on_off_mode:
            self.run_on_off()

        elif self.is_hvac_proportional_mode:
            if self.is_wc_mode:
                self.run_wc()

            if self.is_prop_pid_mode:
                # avoid integral run-off when sum is negative
                if self._wc and self._pid:
                    if (
                        self._wc[ATTR_CONTROL_PWM_OUTPUT] > 0
                        and self._wc[ATTR_CONTROL_PWM_OUTPUT]
                        + self._pid[ATTR_CONTROL_PWM_OUTPUT]
                        < 0
                    ):
                        self.pid_reset_time()
                self.run_pid(force)
                if self._wc and self._pid:
                    if (
                        self._wc[ATTR_CONTROL_PWM_OUTPUT] <= 0
                        and self._pid[ATTR_CONTROL_PWM_OUTPUT] < 0
                    ):
                        # reset integral as wc is also off
                        self.set_integral(0)

        elif self.is_hvac_master_mode:
            # nesting of pwm controlled valves
            start_time = time.time()
            if routine:
                self.nesting.nest_rooms(self._satelites)
                self.nesting.distribute_nesting()
                new_offsets = self.nesting.get_nesting()
                if new_offsets:
                    self.set_satelite_offset(new_offsets)
            # update nesting length only to avoid too large shifts
            else:
                self.nesting.check_pwm(self._satelites, current_offset)
                _ = self.nesting.get_nesting()

            # calculate for proportional valves
            if self.is_valve_mode:
                # avoid integral run-off
                # only include integral when proportional valves are dominant
                if self.valve_pos_pwm_prop < self.valve_pos_pwm_on_off:
                    self.pid_reset_time()
                self.run_pid(force)
            self._logger.debug(
                "Control calculation dt %.4f sec", time.time() - start_time
            )

    def start_master(self):
        """Init the master mode"""
        self._satelites = {}
        self.nesting = pwm_nesting.Nesting(
            self._name,
            operation_mode=self._operation_mode,
            master_pwm=self.pwm_scale,
            tot_area=self.area,
            min_load=self.get_min_load,
        )

    def start_pid(self):
        """Init the PID controller"""
        self._logger.debug("Init pid settings for mode : '%s'", self._hvac_mode)
        self._pid.PID = {}

        if self.is_hvac_proportional_mode:
            mode = CONF_PID_MODE
            lower_pwm_scale, upper_pwm_scale = self.pwm_scale_limits(self._pid)
        else:
            mode = CONF_VALVE_MODE
            lower_pwm_scale = 0
            upper_pwm_scale = 1

        kp, ki, kd = self.get_pid_param(self._pid)  # pylint: disable=invalid-name
        min_cycle_duration = self.get_operate_cycle_time.seconds

        self._pid.PID[PID_CONTROLLER] = pid_controller.PIDController(
            self._name,
            mode,
            min_cycle_duration,
            kp,
            ki,
            kd,
            time.time,
            lower_pwm_scale,
            upper_pwm_scale,
        )

        self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0

    def run_on_off(self):
        """function to determine state switch on_off"""
        # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit
        tolerance_on, tolerance_off = self.get_hysteris
        target_temp = self.target_temperature
        target_temp_min = target_temp - tolerance_on
        target_temp_max = target_temp + tolerance_off
        current_temp = self.current_temperature

        self._logger.debug(
            "on-off - target %s, bandwidth (%s - %s), current %.2f",
            target_temp,
            target_temp_min,
            target_temp_max,
            current_temp,
        )

        if self._hvac_mode == HVACMode.HEAT:
            if current_temp >= target_temp_max:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 0
            elif current_temp <= target_temp_min:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 100
        elif self._hvac_mode == HVACMode.COOL:
            if current_temp <= target_temp_min:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 0
            elif current_temp >= target_temp_max:
                self._on_off[ATTR_CONTROL_PWM_OUTPUT] = 100

    def run_wc(self):
        """calcuate weather compension mode"""
        KA, KB = self.get_ka_kb_param  # pylint: disable=invalid-name
        lower_pwm_scale, upper_pwm_scale = self.pwm_scale_limits(self._wc)

        if self.outdoor_temperature is not None:
            temp_diff = self.target_temperature - self.outdoor_temperature
            self._wc[ATTR_CONTROL_PWM_OUTPUT] = min(
                max(lower_pwm_scale, temp_diff * KA + KB), upper_pwm_scale
            )
        else:
            self._logger.warning("no outdoor temperature; continue with previous data")

    def run_pid(self, force=False):
        """calcuate the PID for current timestep"""
        if self.is_hvac_master_mode:
            current = self.master_max_valve_pos
            setpoint = self.goal
        else:
            if isinstance(self.current_state, (list, tuple, np.ndarray)):
                current = self.current_state

                if self.check_window_open(current[1]):
                    # keep current control_output
                    return
            else:
                current = self.current_temperature
            setpoint = self.target_temperature

        if self.is_hvac_master_mode and current == 0:
            self._pid[ATTR_CONTROL_PWM_OUTPUT] = 0
        else:
            self._pid[ATTR_CONTROL_PWM_OUTPUT] = self._pid.PID[PID_CONTROLLER].calc(
                current, setpoint, force=force, master_mode=self.is_hvac_master_mode
            )

    @property
    def master_max_valve_pos(self):
        """percentage maximum proportional valve opening for valve PID control"""
        max_pwm = 0
        for _, data in self._satelites.items():
            if (
                data[ATTR_HVAC_MODE] == self._hvac_mode
                and data[ATTR_CONTROL_PWM_OUTPUT] is not None
            ):
                if data[CONF_PWM_DURATION] == 0:
                    # pwm as percentage to satelite pwm_scale
                    max_pwm = max(
                        max_pwm, data[ATTR_CONTROL_PWM_OUTPUT] / data[CONF_PWM_SCALE]
                    )

        # scale to master pwm_scale
        return max_pwm

    @property
    def valve_pos_pwm_prop(self):
        """sum of proportional valves scaled to total building area"""
        sum_pwm = 0
        max_pwm = 0
        max_area = 0
        for _, data in self._satelites.items():
            if (
                data[ATTR_HVAC_MODE] == self._hvac_mode
                and data[ATTR_CONTROL_PWM_OUTPUT] is not None
            ):
                if data[CONF_PWM_DURATION] == 0:
                    # self.area in master mode is total building area
                    # pwm as percentage to satelite pwm_scale
                    sum_pwm += (
                        data[ATTR_CONTROL_PWM_OUTPUT]
                        / data[CONF_PWM_SCALE]
                        * data[CONF_AREA]
                    )

                    if data[ATTR_CONTROL_PWM_OUTPUT] / data[CONF_PWM_SCALE] > max_pwm:
                        max_pwm = data[ATTR_CONTROL_PWM_OUTPUT] / data[CONF_PWM_SCALE]
                        max_area = data[CONF_AREA]

        if self._pid:
            # adjust pwm from prop valves to target valve position
            sum_pwm += self._pid[ATTR_CONTROL_PWM_OUTPUT] * max_area

        if sum_pwm > 0:
            sum_pwm /= self.area

        # scale to master pwm_scale
        return sum_pwm * self.pwm_scale

    @property
    def valve_pos_pwm_on_off(self):
        """master pwm based on satelites with pwm controlled on-off valves"""
        return self.nesting.get_master_output()[ATTR_CONTROL_PWM_OUTPUT]

    @property
    def get_control_output(self):
        """Return the control output (offset and valve pos) of the thermostat."""
        control_output = 0
        if self.is_hvac_on_off_mode:
            control_output += self._on_off[ATTR_CONTROL_PWM_OUTPUT]

        elif self.is_hvac_proportional_mode:
            if self.is_prop_pid_mode:
                control_output += self._pid[ATTR_CONTROL_PWM_OUTPUT]
            if self.is_wc_mode:
                control_output += self._wc[ATTR_CONTROL_PWM_OUTPUT]

        elif self.is_hvac_master_mode:
            # Determine valve opening for master valve based on satelites running in
            # proportional hvac mode
            # - get maximal valve opening of satelites with propotional valves
            # - get max opening time from pwm (on/off) valves
            # - take maximum from both
            prop_control = self.valve_pos_pwm_prop
            pwm_on_off = self.valve_pos_pwm_on_off
            control_output = max(prop_control, pwm_on_off)

        if self.is_hvac_master_mode or self.is_hvac_proportional_mode:
            if control_output > self.pwm_scale:
                control_output = self.pwm_scale
            elif control_output < 0:
                control_output = 0

            control_output = get_rounded(
                control_output, self.pwm_scale / self.pwm_resolution
            )
        if self.time_offset is None:
            self.time_offset = 0

        if self.is_hvac_master_mode:
            if self.time_offset + control_output > self.pwm_scale:
                self.time_offset = max(
                    0, self.pwm_scale - (self.time_offset + control_output)
                )

        return {
            ATTR_CONTROL_OFFSET: round(self.time_offset, 3),
            ATTR_CONTROL_PWM_OUTPUT: round(control_output, 3),
        }

    @property
    def time_offset(self):
        """get minimum pwm range"""
        return self._time_offset

    @time_offset.setter
    def time_offset(self, offset):
        """set time offset pwm start"""
        if self.is_hvac_proportional_mode or self.is_hvac_master_mode:
            self._time_offset = offset

    @property
    def min_target_temp(self):
        """return minimum target temperature"""
        if self.is_hvac_master_mode:
            return None
        else:
            return self._hvac_settings[CONF_TARGET_TEMP_MIN]

    @property
    def max_target_temp(self):
        """return maximum target temperature"""
        if self.is_hvac_master_mode:
            return None
        else:
            return self._hvac_settings[CONF_TARGET_TEMP_MAX]

    @property
    def get_target_temp_limits(self):
        """get range of allowed setpoint range"""
        return [
            self.min_target_temp,
            self.max_target_temp,
        ]

    @property
    def target_temperature(self):
        """return target temperature"""
        # initial request
        if self._target_temp is None and not self.is_hvac_master_mode:
            self._target_temp = self._hvac_settings[CONF_TARGET_TEMP_INIT]
        return self._target_temp

    @target_temperature.setter
    def target_temperature(self, target_temp):
        """set new target temperature"""
        self._target_temp = target_temp

    @property
    def get_away_temp(self):
        """return away temp for current hvac mode"""
        if self.is_hvac_on_off_mode or self.is_hvac_proportional_mode:
            return self._hvac_settings.get(CONF_TARGET_TEMP_AWAY)
        else:
            return None

    @property
    def preset_mode(self):
        """get preset mode"""
        return self._preset_mode

    @preset_mode.setter
    def preset_mode(self, mode):
        """set preset mode"""
        if mode == PRESET_EMERGENCY:
            self._old_preset = self.preset_mode
        elif mode == PRESET_RESTORE:
            mode = self._old_preset

        if not self.is_hvac_master_mode and mode not in [
            PRESET_EMERGENCY,
            PRESET_RESTORE,
        ]:
            if self._preset_mode == PRESET_NONE and mode == PRESET_AWAY:
                self.restore_temperature = self.target_temperature
                self.target_temperature = self.get_away_temp

            elif self._preset_mode == PRESET_AWAY and mode == PRESET_NONE:
                if self.restore_temperature is not None:
                    self.target_temperature = self.restore_temperature
                else:
                    self.target_temperature = self._hvac_settings[CONF_TARGET_TEMP_INIT]

        self._preset_mode = mode

    @property
    def get_hvac_switch(self):
        """return the switch entity"""
        return self._switch_entity

    @property
    def is_hvac_switch_on_off(self):
        """check if on-off mode is active"""
        if (
            self.is_hvac_on_off_mode or not self.get_pwm_time.seconds == 0
        ):  # change wpm to on_off or prop
            return True
        else:
            return False

    @property
    def get_hvac_switch_mode(self):
        """return the switch entity"""
        return self._hvac_settings[CONF_SWITCH_MODE]

    @property
    def get_switch_stale(self):
        """return the switch max passive duration"""
        return self._hvac_settings.get(CONF_PASSIVE_SWITCH_DURATION)

    @property
    def stuck_loop(self):
        """return if stuck loop is active"""
        return self._stuck_loop

    @stuck_loop.setter
    def stuck_loop(self, val):
        """set state stuck loop"""
        self._stuck_loop = val

    @property
    def get_pwm_time(self):
        """return pwm interval time"""
        return self.active_control_data.get(CONF_PWM_DURATION, timedelta(seconds=0))

    @property
    def pwm_resolution(self):
        """pwm resolution"""
        return self.active_control_data[CONF_PWM_RESOLUTION]

    @property
    def pwm_scale(self):
        """get deadband range"""
        return self.active_control_data.get(CONF_PWM_SCALE)

    def pwm_scale_limits(self, hvac_data):
        """Bandwidth for control value"""
        upper_pwm_scale = hvac_data.get(CONF_PWM_SCALE_HIGH, self.pwm_scale)
        if CONF_PWM_SCALE_LOW in hvac_data:
            lower_pwm_scale = hvac_data.get(CONF_PWM_SCALE_LOW, 0)
        else:
            if self.is_valve_mode or (self.is_prop_pid_mode and self.is_wc_mode):
                # allow to to negative pwm to compensate
                # - master mode: get valve to goal
                # - prop mode: compensate wc mode
                lower_pwm_scale = -1 * upper_pwm_scale
            else:
                # pwm on-off thermostat dont allow below zero
                lower_pwm_scale = 0

        return [lower_pwm_scale, upper_pwm_scale]

    @property
    def pwm_threshold(self):
        """get minimumactive_control_data pwm range"""
        return self._pwm_threshold

    @pwm_threshold.setter
    def pwm_threshold(self, new_threshold):
        """set minimum pwm"""
        if self.is_hvac_on_off_mode:
            raise ValueError("min diff cannot be set for on-off controller")
        self._pwm_threshold = new_threshold

    @property
    def get_operate_cycle_time(self):
        """return interval for recalculate (control value)"""
        return self.active_control_data.get(
            CONF_CONTROL_REFRESH_INTERVAL, timedelta(seconds=0)
        )

    @property
    def get_min_on_off_cycle(self):
        """minimum duration before recalcute"""
        if self.is_hvac_on_off_mode:
            return self._on_off.get(CONF_MIN_CYCLE_DURATION, timedelta(seconds=0))

    @property
    def get_hysteris(self):
        """get bandwidth for on-off mode"""
        tolerance_on = self._on_off[CONF_HYSTERESIS_TOLERANCE_ON]
        tolerance_off = self._on_off[CONF_HYSTERESIS_TOLERANCE_OFF]

        return [tolerance_on, tolerance_off]

    @property
    def current_state(self):
        """return current temperature and optionally velocity"""
        return self._current_state

    @current_state.setter
    def current_state(self, state):
        self._current_state = state
        if self._current_state:
            self.current_temperature = state[0]

    def set_detailed_output(self, new_mode):
        """change detailed output from service"""
        self._detailed_output = new_mode

    @property
    def current_temperature(self):
        """set new current temperature"""
        return self._current_temperature

    @current_temperature.setter
    def current_temperature(self, current_temp):
        """set new current temperature"""
        self._current_temperature = current_temp

    @property
    def outdoor_temperature(self):
        """set new outdoor temperature"""
        return self._outdoor_temperature

    @outdoor_temperature.setter
    def outdoor_temperature(self, current_temp):
        """set new outdoor temperature"""
        self._outdoor_temperature = current_temp

    def check_window_open(self, current):
        """Return the temperature drop threshold value."""
        if CONF_WINDOW_OPEN_TEMPDROP in self._pid:
            window_threshold = self._pid[CONF_WINDOW_OPEN_TEMPDROP] / 3600
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

    def get_pid_param(self, hvac_data):
        """Return the pid parameters of the thermostat."""
        return (hvac_data.get(ATTR_KP), hvac_data.get(ATTR_KI), hvac_data.get(ATTR_KD))

    def set_pid_param(
        self, kp=None, ki=None, kd=None, update=False
    ):  # pylint: disable=invalid-name
        """Set PID parameters."""
        if kp is not None:
            self._pid[ATTR_KP] = kp
        if ki is not None:
            self._pid[ATTR_KI] = ki
        if kd is not None:
            self._pid[ATTR_KD] = kd

        if update:
            self._pid.PID[PID_CONTROLLER].set_pid_param(kp=kp, ki=ki, kd=kd)

    def pid_reset_time(self):
        """Reset the current time for PID to avoid overflow of the intergral part
        when switching between hvac modes"""
        self._pid.PID[PID_CONTROLLER].reset_time()

    def set_integral(self, integral):
        """function to overwrite integral value"""
        self._pid.PID[PID_CONTROLLER].integral = integral

    @property
    def get_ka_kb_param(self):
        """Return the wc parameters of the thermostat."""
        if self.is_wc_mode:
            ka = self._wc[ATTR_KA]  # pylint: disable=invalid-name
            kb = self._wc[ATTR_KB]  # pylint: disable=invalid-name
            return (ka, kb)
        else:
            return (None, None)

    @property
    def get_wc_sensor(self):
        """return the sensor entity"""
        if self.is_wc_mode:
            return self._wc[CONF_SENSOR_OUT]
        else:
            return None

    def set_ka_kb(self, ka=None, kb=None):  # pylint: disable=invalid-name
        """Set weather mode parameters."""

        if ka is not None:
            self._wc[ATTR_KA] = ka
        if kb is not None:
            self._wc[ATTR_KB] = kb

    @property
    def goal(self):
        """get setpoint for valve mode"""
        return self._pid[ATTR_GOAL]

    @goal.setter
    def goal(self, goal):
        """set setpoint for valve mode"""
        self._pid[ATTR_GOAL] = goal

    @property
    def get_min_load(self):
        """
        master continuous mode factor to set lower bound for heat/cool magnitude
        nesting in continuous mode will use this minimum (as heating area)
        to calculate pwm time duration in low demand conditions eq not
        enough request for continuous operation
        """
        return self._master[CONF_CONTINUOUS_LOWER_LOAD]

    @property
    def get_satelites(self):
        """return the satelite thermostats"""
        if self.is_hvac_master_mode:
            return self._master[CONF_SATELITES]
        else:
            return None

    def update_satelite(
        self, state
    ):  # name, hvac_mode, control_mode, area, valve, offset):
        """set new state for a satelite"""

        sat_name = state.name

        area = state.attributes.get(CONF_AREA)
        self_controlled = state.attributes.get(ATTR_SELF_CONTROLLED)
        update = False

        if (
            state.state
            == self._hvac_mode
            # and control_mode == CONF_PROPORTIONAL_MODE
            # and self_controlled is False
        ):
            self._logger.debug("Save update from '%s'", sat_name)
            control_mode = state.attributes.get(ATTR_HVAC_DEFINITION)[state.state][
                ATTR_CONTROL_MODE
            ]
            preset_mode = state.attributes.get(ATTR_HVAC_DEFINITION)[state.state][
                ATTR_PRESET_MODE
            ]
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

            if preset_mode == PRESET_EMERGENCY:
                control_value = 0

            # check if controller update is needed
            if sat_name in self._satelites:
                if (
                    abs(
                        (
                            control_value
                            - self._satelites[sat_name][ATTR_CONTROL_PWM_OUTPUT]
                        )
                        / max(
                            self._satelites[sat_name][ATTR_CONTROL_PWM_OUTPUT],
                            control_value,
                        )
                    )
                    > PWM_UPDATE_CHANGE
                ):
                    update = True

                if setpoint != self._satelites[sat_name][ATTR_TEMPERATURE]:
                    update = True
            elif control_value > 0:
                update = True

            self._satelites[sat_name] = {
                ATTR_HVAC_MODE: state.state,
                ATTR_SELF_CONTROLLED: self_controlled,
                ATTR_EMERGENCY_MODE: preset_mode,
                ATTR_CONTROL_MODE: control_mode,
                CONF_PWM_DURATION: pwm_time,
                CONF_PWM_SCALE: pwm_scale,
                ATTR_TEMPERATURE: setpoint,
                CONF_AREA: area,
                ATTR_CONTROL_PWM_OUTPUT: control_value,
                ATTR_CONTROL_OFFSET: time_offset,
            }

        elif sat_name in self._satelites:
            self.nesting.remove_room(sat_name)
            self._satelites.pop(sat_name, None)
            update = True

        return update
        # self.master_setpoint()
        # self.master_current_temp()

    @property
    def is_satelite_allowed(self):
        """
        Return if satelite mode is allowed.
        on-off cannot be used with master
        """
        if self.is_hvac_proportional_mode and not self.is_hvac_master_mode:
            return True
        else:
            return False

    def get_satelite_offset(self):
        """PWM offsets for satelites"""
        tmp_dict = {}
        for room, data in self._satelites.items():
            tmp_dict[room] = data[ATTR_CONTROL_OFFSET]
        return tmp_dict

    def set_satelite_offset(self, new_offsets):
        """Store offset per satelite"""
        for room, offset in new_offsets.items():
            if room in self._satelites:
                self._satelites[room][ATTR_CONTROL_OFFSET] = offset

    @property
    def get_control_mode(self):
        """Active control mode"""
        if self.is_hvac_on_off_mode:
            return CONF_ON_OFF_MODE
        elif self.is_hvac_proportional_mode:
            return CONF_PROPORTIONAL_MODE
        elif self.is_hvac_master_mode:
            return CONF_MASTER_MODE

    @property
    def active_control_data(self):
        """get controller data"""
        if self.is_hvac_on_off_mode:
            return self._on_off
        elif self.is_hvac_proportional_mode:
            return self._proportional
        elif self.is_hvac_master_mode:
            return self._master

    @property
    def is_hvac_on_off_mode(self):
        """return the control mode"""
        if self._on_off:
            return True
        else:
            return False

    @property
    def is_hvac_proportional_mode(self):
        """return the control mode"""
        if self._proportional:
            return True
        else:
            return False

    @property
    def is_hvac_master_mode(self):
        """return the control mode"""
        if self._master:
            return True
        else:
            return False

    @property
    def is_prop_pid_mode(self):
        """return the control mode"""
        if self._pid:
            return True
        else:
            return False

    @property
    def is_valve_mode(self):
        """return the control mode"""
        if self.is_hvac_master_mode:
            if ATTR_GOAL in self._master[CONF_VALVE_MODE]:
                return True
            else:
                return False
        else:
            return False

    @property
    def is_wc_mode(self):
        """return the control mode"""
        if self._wc:
            return True
        else:
            return False

    @property
    def get_variable_attr(self):
        """return attributes for climate entity"""
        open_window = None
        if isinstance(self.current_state, (list, tuple, np.ndarray)):
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
        tmp_dict[ATTR_DETAILED_OUTPUT] = self._detailed_output
        tmp_dict["Open_window"] = open_window
        if self.is_hvac_master_mode:
            tmp_dict[CONF_SATELITES] = self.get_satelites
            tmp_dict[CONF_MASTER_OPERATION_MODE] = self._operation_mode
            if self.is_valve_mode:
                tmp_dict["Valve_PID_values"] = self.get_pid_param(self._pid)
                if self._detailed_output:
                    tmp_dict["Valve_PID_P"] = round(
                        self._pid.PID[PID_CONTROLLER].p_var, 3
                    )
                    tmp_dict["Valve_PID_I"] = round(
                        self._pid.PID[PID_CONTROLLER].i_var, 3
                    )
                    tmp_dict["Valve_PID_D"] = round(
                        self._pid.PID[PID_CONTROLLER].d_var, 3
                    )
                    tmp_dict["Valve_PID_valve_pos"] = round(
                        self._pid[ATTR_CONTROL_PWM_OUTPUT], 3
                    )
                elif self._store_integral:
                    tmp_dict["Valve_PID_P"] = None
                    tmp_dict["Valve_PID_I"] = round(
                        self._pid.PID[PID_CONTROLLER].i_var, 3
                    )
                    tmp_dict["Valve_PID_D"] = None
                    tmp_dict["Valve_PID_valve_pos"] = None

        if self.is_hvac_proportional_mode:
            if self.is_prop_pid_mode:
                tmp_dict["PID_values"] = self.get_pid_param(self._pid)
                if self.is_prop_pid_mode:
                    if self._detailed_output:
                        tmp_dict["PID_P"] = round(
                            self._pid.PID[PID_CONTROLLER].p_var, 3
                        )
                        tmp_dict["PID_I"] = round(
                            self._pid.PID[PID_CONTROLLER].i_var, 3
                        )
                        tmp_dict["PID_D"] = round(
                            self._pid.PID[PID_CONTROLLER].d_var, 3
                        )
                        tmp_dict["PID_valve_pos"] = round(
                            self._pid[ATTR_CONTROL_PWM_OUTPUT], 3
                        )
                    elif self._store_integral:
                        tmp_dict["PID_P"] = None
                        tmp_dict["PID_I"] = round(
                            self._pid.PID[PID_CONTROLLER].i_var, 3
                        )
                        tmp_dict["PID_D"] = None
                        tmp_dict["PID_valve_pos"] = None

            if self.is_wc_mode:
                tmp_dict["ab_values"] = self.get_ka_kb_param
                if self._detailed_output:
                    tmp_dict["wc_valve_pos"] = round(
                        self._wc[ATTR_CONTROL_PWM_OUTPUT], 3
                    )
                else:
                    tmp_dict["wc_valve_pos"] = None
        return tmp_dict

    def restore_reboot(self, data, restore_parameters, restore_integral):
        """restore attributes for climate entity"""
        self._store_integral = restore_integral
        self.target_temperature = data[ATTR_TEMPERATURE]

        if self.is_prop_pid_mode:
            if restore_parameters and "PID_values" in data:
                kp, ki, kd = data["PID_values"]  # pylint: disable=invalid-name
                self.set_pid_param(kp=kp, ki=ki, kd=kd, update=True)
        if self.is_valve_mode:
            if restore_parameters and "Valve_PID_values" in data:
                kp, ki, kd = data["Valve_PID_values"]  # pylint: disable=invalid-name
                self.set_pid_param(kp=kp, ki=ki, kd=kd, update=True)

        if restore_integral:
            if self.is_prop_pid_mode:
                if "PID_integral" in data:
                    self._pid.PID[PID_CONTROLLER].integral = data["PID_integral"]
            if self.is_valve_mode:
                if "Valve_PID_integral" in data:
                    self._pid.PID[PID_CONTROLLER].integral = data["Valve_PID_integral"]
        if self._pid:
            self.pid_reset_time()


def get_rounded(input_val, min_clip):
    """https://stackoverflow.com/questions/7859147/round-in-numpy-to-nearest-step"""
    scaled = input_val / min_clip
    return np.where(scaled % 1 >= 0.5, np.ceil(scaled), np.floor(scaled)) * min_clip
