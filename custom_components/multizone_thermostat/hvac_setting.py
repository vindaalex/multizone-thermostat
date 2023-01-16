"""module where configuration of climate is handeled"""
import logging
import time

import numpy as np
from homeassistant.components.climate import ATTR_HVAC_MODE
from homeassistant.const import CONF_ENTITY_ID

from . import pid_controller, pwm_nesting
from .const import (  # HVACMode.COOL,; HVACMode.HEAT,; on_off thermostat; proportional mode; CONF_VALVE_DELAY,; PID controller; weather compensating mode; Master mode; valve_control_mode
    CONF_AWAY_TEMP,
    CONF_CONTROL_REFRESH_INTERVAL,
    CONF_GOAL,
    CONF_HVAC_DEFINITION,
    CONF_HVAC_MODE_INIT_TEMP,
    CONF_HVAC_MODE_MAX_TEMP,
    CONF_HVAC_MODE_MIN_TEMP,
    CONF_HYSTERESIS_TOLERANCE_OFF,
    CONF_HYSTERESIS_TOLERANCE_ON,
    CONF_KA,
    CONF_KB,
    CONF_KD,
    CONF_KI,
    CONF_KP,
    CONF_MASTER_MODE,
    CONF_MAX_DIFFERENCE,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_DIFF,
    CONF_MIN_DIFFERENCE,
    CONF_MIN_LOAD,
    CONF_ON_OFF_MODE,
    CONF_OPERATION,
    CONF_PASSIVE_SWITCH_DURATION,
    CONF_PID_MODE,
    CONF_PROPORTIONAL_MODE,
    CONF_PWM,
    CONF_PWM_RESOLUTION,
    CONF_PWM_SCALE,
    CONF_SATELITES,
    CONF_SENSOR_OUT,
    CONF_SWITCH_MODE,
    CONF_WC_MODE,
    CONF_WINDOW_OPEN_TEMPDROP,
    CONTROL_OUTPUT,
    PID_CONTROLLER,
    PRESET_AWAY,
    PRESET_NONE,
    PROP_PID_MODE,
    VALVE_PID_MODE,
    VALVE_POS,
    HVACMode,
)


class HVACSetting:
    """definition of hvac mode"""

    def __init__(self, log_id, hvac_mode, conf, area):
        self._logger = logging.getLogger(log_id).getChild(hvac_mode)
        self._logger.info("Config hvac settings for hvac_mode : '%s'", hvac_mode)

        self._hvac_mode = hvac_mode
        self._preset_mode = PRESET_NONE
        self._hvac_settings = conf
        self._switch_entity = self._hvac_settings[CONF_ENTITY_ID]
        self._switch_mode = self._hvac_settings[CONF_SWITCH_MODE]
        self.area = area

        self._target_temp = None
        self._current_state = None
        self._current_temperature = None
        self._outdoor_temperature = None
        self.restore_temperature = None

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
            self._operation_mode = self._master.get(CONF_OPERATION)
            self._pid = self._master.get(VALVE_PID_MODE)

        self.init_mode()

    def init_mode(self):
        """init the defined control modes"""
        if self.is_hvac_on_off_mode:
            self._logger.debug("HVAC mode 'on_off' active")
            self.start_on_off()
            self._on_off[CONTROL_OUTPUT] = 0
        if self.is_hvac_proportional_mode:
            self._logger.debug("HVAC mode 'proportional' active")
            if self.is_prop_pid_mode:
                self._logger.debug("HVAC mode 'pid' active")
                self.start_pid()
                self._pid[CONTROL_OUTPUT] = 0
            if self.is_wc_mode:
                self._logger.debug("HVAC mode 'weather control' active")
                self._wc[CONTROL_OUTPUT] = 0
        if self.is_hvac_master_mode:
            self._logger.debug("HVAC mode 'master' active")
            self.start_master()
            if self.is_valve_mode:
                self._logger.debug("HVAC mode 'valve control' active")
                self.start_pid()
                self._pid[CONTROL_OUTPUT] = 0

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
                        self._wc[CONTROL_OUTPUT] > 0
                        and self._wc[CONTROL_OUTPUT] + self._pid[CONTROL_OUTPUT] < 0
                    ):
                        self.pid_reset_time()
                self.run_pid(force)
                if self._wc and self._pid:
                    if self._wc[CONTROL_OUTPUT] == 0 and self._pid[CONTROL_OUTPUT] < 0:
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

    def start_on_off(self):
        """set basic settings for hysteris mode"""
        self._logger.debug("Init on_off settings for mode : '%s'", self._hvac_mode)
        if CONF_CONTROL_REFRESH_INTERVAL not in self._on_off:
            self._on_off[CONF_CONTROL_REFRESH_INTERVAL] = None

    def start_master(self):
        """Init the master mode"""
        self._satelites = {}
        self.nesting = pwm_nesting.Nesting(
            self._logger,
            operation_mode=self._operation_mode,
            master_pwm=self.pwm_scale,
            tot_area=self.area,
            # min_diff=self.min_diff,
            min_load=self.get_min_load,
        )

    def start_pid(self):
        """Init the PID controller"""
        self._logger.debug("Init pid settings for mode : '%s'", self._hvac_mode)
        self._pid.PID = {}

        if self.is_hvac_proportional_mode:
            mode = PROP_PID_MODE
        else:
            mode = VALVE_PID_MODE

        lower_pwm_scale, upper_pwm_scale = self.pwm_scale_limits(self._pid)
        kp, ki, kd = self.get_pid_param(self._pid)  # pylint: disable=invalid-name
        min_cycle_duration = self.get_operate_cycle_time.seconds

        self._pid.PID[PID_CONTROLLER] = pid_controller.PIDController(
            self._logger.name,
            mode,
            min_cycle_duration,
            kp,
            ki,
            kd,
            time.time,
            lower_pwm_scale,
            upper_pwm_scale,
        )

        self._pid[CONTROL_OUTPUT] = 0

    def run_on_off(self):
        """function to determine state switch on_off"""
        # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit
        tolerance_on, tolerance_off = self.get_hysteris
        target_temp = self.target_temperature
        target_temp_min = target_temp - tolerance_on
        target_temp_max = target_temp + tolerance_off
        current_temp = self.current_temperature

        self._logger.debug(
            "Operate - tg_min %s, tg_max %s, current %s, tg '%s'",
            target_temp_min,
            target_temp_max,
            current_temp,
            target_temp,
        )

        if self._hvac_mode == HVACMode.HEAT:
            if current_temp >= target_temp_max:
                self._on_off[CONTROL_OUTPUT] = 0
            elif current_temp <= target_temp_min:
                self._on_off[CONTROL_OUTPUT] = 100
        elif self._hvac_mode == HVACMode.COOL:
            if current_temp <= target_temp_min:
                self._on_off[CONTROL_OUTPUT] = 0
            elif current_temp >= target_temp_max:
                self._on_off[CONTROL_OUTPUT] = 100

    def run_wc(self):
        """calcuate weather compension mode"""
        KA, KB = self.get_ka_kb_param  # pylint: disable=invalid-name
        _, upper_pwm_scale = self.pwm_scale_limits(self._wc)

        if self.outdoor_temperature is not None:
            temp_diff = self.target_temperature - self.outdoor_temperature
            self._wc[CONTROL_OUTPUT] = min(max(0, temp_diff * KA + KB), upper_pwm_scale)
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
            self._pid[CONTROL_OUTPUT] = 0
        else:
            self._pid[CONTROL_OUTPUT] = self._pid.PID[PID_CONTROLLER].calc(
                current, setpoint, force=force, master_mode=self.is_hvac_master_mode
            )

    @property
    def master_max_valve_pos(self):
        """maximum proportional valve opening for valve PID control"""
        max_pwm = 0
        for _, data in self._satelites.items():
            if data[ATTR_HVAC_MODE] == self._hvac_mode and data[VALVE_POS] is not None:
                if data["pwm_time"] == 0:
                    max_pwm = max(max_pwm, data[VALVE_POS])

        return max_pwm

    @property
    def valve_pos_pwm_prop(self):
        """sum of proportional valves scaled to total building area"""
        max_pwm = 0
        for _, data in self._satelites.items():
            if data[ATTR_HVAC_MODE] == self._hvac_mode and data[VALVE_POS] is not None:
                if data["pwm_time"] == 0:
                    # self.area in master mode is total building area
                    max_pwm += data[VALVE_POS] * data["area"]
        if max_pwm > 0:
            max_pwm /= self.area

        return max_pwm

    @property
    def valve_pos_pwm_on_off(self):
        """master pwm based on satelites with pwm controlled on-off valves"""
        return self.nesting.get_master_output()[1]

    @property
    def get_control_output(self):
        """Return the control output of the thermostat."""
        control_output = 0
        if self.is_hvac_on_off_mode:
            control_output += self._on_off[CONTROL_OUTPUT]

        elif self.is_hvac_proportional_mode:
            if self.is_prop_pid_mode:
                control_output += self._pid[CONTROL_OUTPUT]
            if self.is_wc_mode:
                control_output += self._wc[CONTROL_OUTPUT]

        elif self.is_hvac_master_mode:
            # Determine valve opening for master valve based on satelites running in
            # proportional hvac mode
            # - get maximal valve opening of satelites with propotional valves
            # - get max opening time from pwm (on/off) valves
            # - take maximum from both
            prop_control = self.valve_pos_pwm_prop
            if self._pid:
                # adjust pwm from prop valves to target valve position
                prop_control += self._pid[CONTROL_OUTPUT]
            pwm_on_off = self.valve_pos_pwm_on_off
            control_output = max(prop_control, pwm_on_off)

        if control_output > self.pwm_scale:
            control_output = self.pwm_scale
        elif control_output < 0:
            control_output = 0

            control_output = getRoundedThresholdv1(control_output, self.pwm_resolution)
        if self.time_offset is None:
            self.time_offset = 0

        return {
            "offset": round(self.time_offset, 3),
            "output": round(control_output, 3),
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
            return self.active_control_data[CONF_HVAC_MODE_MIN_TEMP]

    @property
    def max_target_temp(self):
        """return maximum target temperature"""
        if self.is_hvac_master_mode:
            return None
        else:
            return self.active_control_data[CONF_HVAC_MODE_MAX_TEMP]

    @property
    def get_target_temp_limits(self):
        """get range of allowed setpoint range"""
        return [
            self.active_control_data[CONF_HVAC_MODE_MIN_TEMP],
            self.active_control_data[CONF_HVAC_MODE_MAX_TEMP],
        ]

    @property
    def target_temperature(self):
        """return target temperature"""
        # initial request
        if self._target_temp is None and not self.is_hvac_master_mode:
            self._target_temp = self.active_control_data[CONF_HVAC_MODE_INIT_TEMP]
        return self._target_temp

    @target_temperature.setter
    def target_temperature(self, target_temp):
        """set new target temperature"""
        self._target_temp = target_temp

    @property
    def get_away_temp(self):
        """return away temp for current hvac mode"""
        if self.is_hvac_on_off_mode or self.is_hvac_proportional_mode:
            return self.active_control_data[CONF_AWAY_TEMP]
        else:
            return None

    @property
    def preset_mode(self):
        """get preset mode"""
        return self._preset_mode

    @preset_mode.setter
    def preset_mode(self, mode):
        """set preset mode"""
        if not self.is_hvac_master_mode:
            if self._preset_mode == PRESET_NONE and mode == PRESET_AWAY:
                self.restore_temperature = self.target_temperature
                self.target_temperature = self.get_away_temp

            elif self._preset_mode == PRESET_AWAY and mode == PRESET_NONE:
                if self.restore_temperature is not None:
                    self.target_temperature = self.restore_temperature
                else:
                    self.target_temperature = self.active_control_data[
                        CONF_HVAC_MODE_INIT_TEMP
                    ]

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
        return self._switch_mode

    @property
    def get_switch_stale(self):
        """return the switch max passive duration"""
        if CONF_PASSIVE_SWITCH_DURATION in self._hvac_settings:
            return self._hvac_settings[CONF_PASSIVE_SWITCH_DURATION]
        else:
            return None

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
        if self.is_hvac_proportional_mode:
            # if self.master_control_interval is not None:
            #     return self.master_control_interval
            # else:
            return self._proportional[CONF_PWM]
        elif self.is_hvac_master_mode:
            return self._master[CONF_PWM]
        else:
            return None

    @property
    def pwm_resolution(self):
        """pwm resolution"""
        return self.active_control_data[CONF_PWM_RESOLUTION]

    # @property
    # def master_control_interval(self):
    #     """get control interval range"""
    #     return self._master_control_interval

    # @master_control_interval.setter
    # def master_control_interval(self, interval):
    #     """set control interval for satelite"""
    #     if self.is_hvac_proportional_mode:
    #         self._master_control_interval = interval

    # @property
    # def get_valve_delay(self):
    #     """get delay to open valve"""
    #     if self.is_hvac_proportional_mode:
    #         return self._proportional[CONF_VALVE_DELAY]
    #     else:
    #         return 0

    @property
    def pwm_scale(self):
        """get deadband range"""
        if self.is_hvac_proportional_mode:
            return self._proportional[CONF_PWM_SCALE]
        elif self.is_hvac_master_mode:
            return self._master[CONF_PWM_SCALE]
        else:
            return None

    def pwm_scale_limits(self, hvac_data):
        """Bandwitdh for control value"""
        if any(x for x in (CONF_MIN_DIFFERENCE, CONF_MAX_DIFFERENCE) if x in hvac_data):
            if CONF_MIN_DIFFERENCE in hvac_data:
                lower_pwm_scale = hvac_data[CONF_MIN_DIFFERENCE]
            else:
                lower_pwm_scale = 0
            if CONF_MAX_DIFFERENCE in hvac_data:
                upper_pwm_scale = hvac_data[CONF_MAX_DIFFERENCE]
            else:
                upper_pwm_scale = self.pwm_scale
        else:
            difference = self.pwm_scale
            if self.is_valve_mode or (self.is_prop_pid_mode and self.is_wc_mode):
                # allow to to negative pwm to compensate
                # - master mode: get valve to goal
                # - prop mode: compensate wc mode
                lower_pwm_scale = -1 * difference
            else:
                # pwm on-off thermostat dont allow below zero
                lower_pwm_scale = 0

            upper_pwm_scale = difference

        return [lower_pwm_scale, upper_pwm_scale]

    @property
    def min_diff(self):
        """get minimum pwm range"""
        if self.is_hvac_proportional_mode:
            return self._proportional[CONF_MIN_DIFF]
        elif self.is_hvac_master_mode:
            return self._master[CONF_MIN_DIFF]
        else:
            # on-off will give control ouput 0 or 100, 50 is toggle point
            return 50

    @min_diff.setter
    def min_diff(self, min_diff):
        """set minimum pwm"""
        if self.is_hvac_proportional_mode:
            self._proportional[CONF_MIN_DIFF] = min_diff
        elif self.is_hvac_master_mode:
            self._master[CONF_MIN_DIFF] = min_diff

    @property
    def get_operate_cycle_time(self):
        """return interval for recalculate (control value)"""
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_CONTROL_REFRESH_INTERVAL]
        elif self.is_hvac_proportional_mode:
            # if self.master_control_interval is not None:
            #     return self.master_control_interval
            # else:
            return self._proportional[CONF_CONTROL_REFRESH_INTERVAL]
        elif self.is_hvac_master_mode:
            # controller and pwm routine are equal (for now)
            # return self.get_pwm_time
            return self._master[CONF_CONTROL_REFRESH_INTERVAL]

    @property
    def get_min_on_off_cycle(self):
        """minimum duration before recalcute"""
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_MIN_CYCLE_DURATION]

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
                self._logger.warning(
                    "temperature drop %s: open window detected, maintain old control value",
                    round(current, 5),
                )
                return True
        elif self._hvac_mode == HVACMode.COOL:
            if current > window_threshold:
                self._logger.warning(
                    "temperature rise %s: open window detected, maintain old control value",
                    round(current, 5),
                )
                return True

    def get_pid_param(self, hvac_data):
        """Return the pid parameters of the thermostat."""
        kp = None  # pylint: disable=invalid-name
        ki = None  # pylint: disable=invalid-name
        kd = None  # pylint: disable=invalid-name

        if CONF_KP in hvac_data:
            kp = hvac_data[CONF_KP]  # pylint: disable=invalid-name
        if CONF_KI in hvac_data:
            ki = hvac_data[CONF_KI]  # pylint: disable=invalid-name
        if CONF_KD in hvac_data:
            kd = hvac_data[CONF_KD]  # pylint: disable=invalid-name
        return (kp, ki, kd)

    def set_pid_param(
        self, kp=None, ki=None, kd=None, update=False
    ):  # pylint: disable=invalid-name
        """Set PID parameters."""
        if kp is not None:
            self._pid[CONF_KP] = kp
        if ki is not None:
            self._pid[CONF_KI] = ki
        if kd is not None:
            self._pid[CONF_KD] = kd

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
            ka = self._wc[CONF_KA]  # pylint: disable=invalid-name
            kb = self._wc[CONF_KB]  # pylint: disable=invalid-name
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
            self._wc[CONF_KA] = ka
        if kb is not None:
            self._wc[CONF_KB] = kb

    @property
    def goal(self):
        """get setpoint for valve mode"""
        return self._pid[CONF_GOAL]

    @goal.setter
    def goal(self, goal):
        """set setpoint for valve mode"""
        self._pid[CONF_GOAL] = goal

    @property
    def get_min_load(self):
        return self._master[CONF_MIN_LOAD]

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

        area = state.attributes.get("room_area")
        self_controlled = state.attributes.get("self_controlled")
        update = False

        if (
            state.state
            == self._hvac_mode
            # and control_mode == CONF_PROPORTIONAL_MODE
            # and self_controlled is False
        ):
            self._logger.debug("Save update from '%s'", sat_name)
            control_mode = state.attributes.get(CONF_HVAC_DEFINITION)[state.state][
                "control_mode"
            ]
            pwm_time = state.attributes.get(CONF_HVAC_DEFINITION)[state.state][
                "pwm_time"
            ]
            pwm_scale = state.attributes.get(CONF_HVAC_DEFINITION)[state.state][
                "pwm_scale"
            ]
            setpoint = state.attributes["temperature"]
            time_offset, control_value = state.attributes.get(CONF_HVAC_DEFINITION)[
                state.state
            ][CONTROL_OUTPUT].values()

            # check if controller update is needed
            if sat_name in self._satelites:
                if self._satelites[sat_name][VALVE_POS] == 0:
                    pass
                # if valve pos changed too much
                elif (
                    abs(
                        (control_value - self._satelites[sat_name][VALVE_POS])
                        / self._satelites[sat_name][VALVE_POS]
                    )
                    > 0.05
                ):
                    update = True

                if setpoint != self._satelites[sat_name]["setpoint"]:
                    update = True
            else:
                update = True

            self._satelites[sat_name] = {
                ATTR_HVAC_MODE: state.state,
                "self_controlled": self_controlled,
                "control_mode": control_mode,
                "pwm_time": pwm_time,
                "pwm_scale": pwm_scale,
                "setpoint": setpoint,
                # "current": current,
                "area": area,
                VALVE_POS: control_value,
                "time_offset": time_offset,
            }

        else:
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
        tmp_dict = {}
        for room, data in self._satelites.items():
            tmp_dict[room] = data["time_offset"]
        return tmp_dict

    def set_satelite_offset(self, new_offsets):

        for room, offset in new_offsets.items():
            if room in self._satelites:
                self._satelites[room]["time_offset"] = offset

    @property
    def get_control_mode(self):
        if self.is_hvac_on_off_mode:
            return CONF_ON_OFF_MODE
        elif self.is_hvac_proportional_mode:
            return CONF_PROPORTIONAL_MODE
        elif self.is_hvac_master_mode:
            return CONF_MASTER_MODE

    @property
    def active_control_data(self):
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
            if CONF_GOAL in self._pid:
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
        tmp_dict = {}
        tmp_dict["target_temp"] = self.target_temperature
        tmp_dict["satelite_allowed"] = self.is_satelite_allowed
        tmp_dict["control_mode"] = self.get_control_mode
        tmp_dict["control_interval"] = self.get_operate_cycle_time.seconds
        tmp_dict["pwm_time"] = self.get_pwm_time.seconds
        tmp_dict["pwm_scale"] = self.pwm_scale
        tmp_dict[CONTROL_OUTPUT] = self.get_control_output

        if self.is_hvac_master_mode:
            tmp_dict["satelites"] = self.get_satelites
            if self.is_valve_mode:
                tmp_dict["Valve_PID_values"] = self.get_pid_param(self._pid)
                # if self._master.PID[PID_CONTROLLER]:
                tmp_dict["Valve_PID_P"] = round(self._pid.PID[PID_CONTROLLER].p_var, 3)
                tmp_dict["Valve_PID_I"] = round(self._pid.PID[PID_CONTROLLER].i_var, 3)
                tmp_dict["Valve_PID_D"] = round(self._pid.PID[PID_CONTROLLER].d_var, 5)
                tmp_dict["Valve_PID_valve_pos"] = self._pid[CONTROL_OUTPUT]

        if self.is_hvac_proportional_mode:
            # tmp_dict[VALVE_POS] = self.get_control_output
            if self.is_prop_pid_mode:
                tmp_dict["PID_values"] = self.get_pid_param(self._pid)
                if self._pid.PID[PID_CONTROLLER]:
                    tmp_dict["PID_P"] = round(self._pid.PID[PID_CONTROLLER].p_var, 3)
                    tmp_dict["PID_I"] = round(self._pid.PID[PID_CONTROLLER].i_var, 3)
                    tmp_dict["PID_D"] = round(self._pid.PID[PID_CONTROLLER].d_var, 5)
                tmp_dict["PID_valve_pos"] = round(self._pid[CONTROL_OUTPUT], 3)

            if self.is_wc_mode:
                tmp_dict["ab_values"] = self.get_ka_kb_param
                tmp_dict["wc_valve_pos"] = round(self._wc[CONTROL_OUTPUT], 3)
        return tmp_dict

    def restore_reboot(self, data, restore_parameters, restore_integral):
        """restore attributes for climate entity"""
        self.target_temperature = data["target_temp"]

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

        self.pid_reset_time()


def getRoundedThresholdv1(a, MinClip):
    """https://stackoverflow.com/questions/7859147/round-in-numpy-to-nearest-step"""
    scaled = a / MinClip
    return np.where(scaled % 1 >= 0.5, np.ceil(scaled), np.floor(scaled)) * MinClip
