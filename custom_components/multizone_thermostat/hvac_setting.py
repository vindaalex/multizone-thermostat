"""module where configuration of climate is handeled"""
from . import pid_controller
import time
import logging
import numpy as np

from .const import (
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    PRESET_AWAY,
    PRESET_NONE,
    CONF_ENTITY_ID,
    CONF_HVAC_MODE_INIT_TEMP,
    CONF_HVAC_MODE_MIN_TEMP,
    CONF_HVAC_MODE_MAX_TEMP,
    CONF_AWAY_TEMP,
    CONF_PASSIVE_SWITCH_DURATION,
    # on_off thermostat
    CONF_ON_OFF_MODE,
    CONF_MIN_CYCLE_DURATION,
    CONF_KEEP_ALIVE,
    CONF_HYSTERESIS_TOLERANCE_ON,
    CONF_HYSTERESIS_TOLERANCE_OFF,
    # proportional mode
    CONF_PROPORTIONAL_MODE,
    CONF_PWM,
    CONF_CONTROL_REFRESH_INTERVAL,
    CONF_DIFFERENCE,
    CONF_RESOLUTION,
    CONF_MIN_DIFFERENCE,
    CONF_MAX_DIFFERENCE,
    CONF_MIN_DIFF,
    CONF_WINDOW_OPEN_TEMPDROP,
    # PID controller
    CONF_PID_MODE,
    CONF_KP,
    CONF_KI,
    CONF_KD,
    # weather compensating mode
    CONF_WC_MODE,
    CONF_SENSOR_OUT,
    CONF_KA,
    CONF_KB,
    # Master mode
    CONF_MASTER_MODE,
    CONF_SATELITES,
    # valve_control_mode
    CONF_GOAL,
)

# from .const.defaults_controller import *


class HVACSetting:
    """definition of hvac mode"""

    def __init__(self, log_id, mode, conf):
        self._logger = logging.getLogger(log_id).getChild(mode)
        self._logger.info("Config hvac settings for mode : %s", mode)

        self._mode = mode
        self._preset_mode = PRESET_NONE
        self._hvac_settings = conf
        self._swtich_entity = self._hvac_settings[CONF_ENTITY_ID]

        self.target_temperature = self._hvac_settings[CONF_HVAC_MODE_INIT_TEMP]
        self._away_temp = self._hvac_settings[CONF_AWAY_TEMP]
        self._current_state = None
        self._current_temperature = None
        self._outdoor_temperature = None

        self._on_off = None
        self._proportional = None
        self._pid = None
        self._wc = None
        self._master = None
        self._satelites = None
        self._master_max_valve_pos = None
        self._resolution = 0        

        self._stuck_loop = False

        self._on_off = self._hvac_settings.get(CONF_ON_OFF_MODE)
        self._proportional = self._hvac_settings.get(CONF_PROPORTIONAL_MODE)

        if self.is_hvac_proportional_mode:
            self._wc = self._proportional.get(CONF_WC_MODE)
            self._pid = self._proportional.get(CONF_PID_MODE)
            self._master = self._proportional.get(CONF_MASTER_MODE)
            self._resolution = self._proportional[CONF_RESOLUTION]

        self.init_mode()

    def init_mode(self):
        """init the defined control modes"""
        if self.is_hvac_on_off_mode:
            self._logger.debug("HVAC mode 'on_off' active")
            self.start_on_off()
            self._on_off["control_output"] = 0
        if self.is_hvac_proportional_mode:
            self._logger.debug("HVAC mode 'proportional' active")
            if self.is_master_mode:
                self._logger.debug("HVAC mode 'master' active")
                self.start_master()
                self._master_max_valve_pos = 0
                if self.is_hvac_valve_mode:
                    self._logger.debug("HVAC mode 'valve control' active")
                    self.start_pid(self._master)
                    self._master["control_output"] = 0
            if self.is_hvac_pid_mode:
                self._logger.debug("HVAC mode 'pid' active")
                self.start_pid(self._pid)
                self._pid["control_output"] = 0
            if self.is_hvac_wc_mode:
                self._logger.debug("HVAC mode 'weather control' active")
                self._wc["control_output"] = 0

    def calculate(self, force=None):
        """Calculate the current control values for all activated modes"""
        if self.is_hvac_on_off_mode:
            self.run_on_off()
        else:
            if self.is_hvac_pid_mode or self.is_hvac_valve_mode:
                self.run_pid(force)
            if self.is_hvac_wc_mode:
                self.run_wc()

    @property
    def min_target_temp(self):
        """return minimum target temperature"""
        if self.is_master_mode:  # min/max similar for master
            return self.target_temperature
        else:
            return self._hvac_settings[CONF_HVAC_MODE_MIN_TEMP]

    @property
    def max_target_temp(self):
        """return maximum target temperature"""
        if self.is_master_mode:  # min/max similar for master
            return self.target_temperature
        else:
            return self._hvac_settings[CONF_HVAC_MODE_MAX_TEMP]

    def start_on_off(self):
        """set basic settings for hysteris mode"""
        self._logger.debug("Init on_off settings for mode : %s", self._mode)
        try:
            self._on_off[CONF_KEEP_ALIVE]
        except:  # pylint: disable=bare-except
            self._on_off[CONF_KEEP_ALIVE] = None

    def start_master(self):
        """Init the master mode"""
        self._satelites = {}

    def start_pid(self, hvac_data):
        """Init the PID controller"""
        self._logger.debug("Init pid settings for mode : %s", self._mode)
        hvac_data = self.get_hvac_data(hvac_data)
        hvac_data.PID = {}

        if CONF_GOAL in hvac_data:
            mode = "valve_pid"
        else:
            mode = "pid"

        min_diff, max_diff = self.get_difference_limits(hvac_data)
        kp, ki, kd = self.get_pid_param(hvac_data)  # pylint: disable=invalid-name
        min_cycle_duration = self.get_operate_cycle_time

        hvac_data.PID["pidController"] = pid_controller.PIDController(
            self._logger.name,
            mode,
            min_cycle_duration.seconds,
            kp,
            ki,
            kd,
            time.time,
            min_diff,
            max_diff,
        )

        hvac_data["control_output"] = 0

    def run_on_off(self):
        """function to determine state switch on_off"""
        # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit
        tolerance_on, tolerance_off = self.get_hysteris
        target_temp = self.target_temperature
        target_temp_min = target_temp - tolerance_on
        target_temp_max = target_temp + tolerance_off
        current_temp = self.current_temperature

        self._logger.debug(
            "Operate - tg_min %s, tg_max %s, current %s, tg %s",
            target_temp_min,
            target_temp_max,
            current_temp,
            target_temp,
        )

        if self._mode == HVAC_MODE_HEAT:
            if current_temp >= target_temp_max:
                self._on_off["control_output"] = 0
            elif current_temp <= target_temp_min:
                self._on_off["control_output"] = 100
        elif self._mode == HVAC_MODE_COOL:
            if current_temp <= target_temp_min:
                self._on_off["control_output"] = 0
            elif current_temp >= target_temp_max:
                self._on_off["control_output"] = 100

    def run_wc(self):
        """calcuate weather compension mode"""
        KA, KB = self.get_ka_kb_param  # pylint: disable=invalid-name
        hvac_data = self.get_hvac_data("wc")
        _, max_diff = self.get_difference_limits(hvac_data)

        if self.outdoor_temperature is not None:
            temp_diff = self.target_temperature - self.outdoor_temperature
            self._wc["control_output"] = min(max(0, temp_diff * KA + KB), max_diff)
        else:
            self._logger.warning("no outdoor temperature; continue with previous data")

    def run_pid(self, force=False):
        """calcuate the PID for current timestep"""
        hvacs = []
        if self.is_hvac_pid_mode:
            hvacs.append(self._pid)
        if self.is_hvac_valve_mode:
            hvacs.append(self._master)

        for hvac_data in hvacs:
            if CONF_SATELITES in hvac_data:
                current = self._master_max_valve_pos
                current_temp = current
                setpoint = self.goal
            else:
                if isinstance(self.current_state, (list, tuple, np.ndarray)):
                    current = self.current_state

                    if self.check_window_open(hvac_data, current[1]):
                        # keep current control_output
                        break

                    current_temp = current[0]
                else:
                    current = self.current_temperature
                    current_temp = current
                setpoint = self.target_temperature

            if CONF_SATELITES in hvac_data and current_temp == 0:
                hvac_data["control_output"] = 0
            else:
                hvac_data["control_output"] = hvac_data.PID["pidController"].calc(
                    current,
                    setpoint,
                    force=force,
                    master_mode=self.is_master_mode
                )

    @property
    def get_control_output(self):
        """Return the control output of the thermostat."""
        key = "control_output"
        control_output = 0
        if self.is_hvac_on_off_mode:
            control_output += self._on_off[key]
        else:
            if self.is_hvac_pid_mode:
                control_output += self._pid[key]
            if self.is_hvac_wc_mode:
                control_output += self._wc[key]
            if self.is_hvac_valve_mode:
                control_output += self._master[key]

            if self.is_hvac_valve_mode:
                if self._master_max_valve_pos == 0:
                    control_output = 0

            if control_output > self.get_difference:
                control_output = self.get_difference
            elif control_output < 0:
                control_output = 0

            control_output = getRoundedThresholdv1(control_output, self._resolution) 
        return control_output

    @property
    def target_temperature(self):
        """return target temperature"""
        return self._target_temp

    @target_temperature.setter
    def target_temperature(self, target_temp):
        """set new target temperature"""
        self._target_temp = target_temp

    @property
    def preset_mode(self):
        """get preset mode"""
        return self._preset_mode

    @preset_mode.setter
    def preset_mode(self, mode):
        """set preset mode"""
        self._preset_mode = mode
        if self._preset_mode == PRESET_AWAY:
            self.target_temperature = self.get_away_temp

    @property
    def get_hvac_switch(self):
        """return the switch entity"""
        return self._swtich_entity

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
    def get_target_temp_limits(self):
        """get range of allowed setpoint range"""
        return [
            self._hvac_settings[CONF_HVAC_MODE_MIN_TEMP],
            self._hvac_settings[CONF_HVAC_MODE_MAX_TEMP],
        ]

    @property
    def get_away_temp(self):
        """return away temp for current hvac mode"""
        return self._away_temp

    @property
    def get_pwm_mode(self):
        """return pwm interval time"""
        if self.is_hvac_proportional_mode:
            return self._proportional[CONF_PWM]
        else:
            return None

    @property
    def get_difference(self):
        """get deadband range"""
        if self.is_hvac_proportional_mode:
            return self._proportional[CONF_DIFFERENCE]
        else:
            return None

    @property
    def min_diff(self):
        """get minimum pwm range"""
        if self.is_hvac_proportional_mode:
            return self._proportional[CONF_MIN_DIFF]
        else:
            return 50

    @min_diff.setter
    def min_diff(self, min_diff):
        """set minimum pwm"""
        if self.is_hvac_proportional_mode:
            self._proportional[CONF_MIN_DIFF] = min_diff

    @property
    def get_operate_cycle_time(self):
        """return interval for recalcuate (control value)"""
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_KEEP_ALIVE]
        elif self.is_hvac_proportional_mode:
            return self._proportional[CONF_CONTROL_REFRESH_INTERVAL]

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

    def get_hvac_data(self, hvac_data):
        """get the controller data"""
        if isinstance(hvac_data, str):
            if hvac_data == "pid":
                if self.is_hvac_pid_mode:
                    return self._pid
                else:
                    return None
            elif hvac_data == "valve":
                if self.is_master_mode:
                    return self._master
                else:
                    return None
            elif hvac_data == "wc":
                if self.is_hvac_wc_mode:
                    return self._wc
                else:
                    return None

            else:
                return None
        elif hvac_data:
            return hvac_data
        else:
            return None

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

    def get_difference_limits(self, hvac_data):
        """Bandwitdh for control value"""
        hvac_data = self.get_hvac_data(hvac_data)

        present_data = [
            x for x in [CONF_MIN_DIFFERENCE, CONF_MAX_DIFFERENCE] if x in hvac_data
        ]
        if present_data:
            if CONF_MIN_DIFFERENCE in hvac_data:
                min_diff = hvac_data[CONF_MIN_DIFFERENCE]
            else:
                min_diff = 0
            if CONF_MAX_DIFFERENCE in hvac_data:
                max_diff = hvac_data[CONF_MAX_DIFFERENCE]
            else:
                max_diff = self.get_difference

        else:
            difference = self.get_difference
            if (
                self.is_hvac_valve_mode
                and (self.is_hvac_wc_mode or self.is_hvac_pid_mode)
            ) or (
                self.is_hvac_pid_mode
                and (self.is_hvac_wc_mode or self.is_hvac_valve_mode)
            ):
                min_diff = -1 * difference
            else:
                min_diff = 0

            max_diff = difference

        return [min_diff, max_diff]

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

    def check_window_open(self, hvac_data, current):
        """Return the temperature drop threshold value."""
        if CONF_WINDOW_OPEN_TEMPDROP in hvac_data:
            window_threshold = hvac_data[CONF_WINDOW_OPEN_TEMPDROP] / 3600
        else:
            return False

        if self._mode == HVAC_MODE_HEAT:
            if current < window_threshold:
                self._logger.warning(
                    "temperature drop %s: open window detected, maintain old control value",
                    round(current, 5),
                )
                return True
        elif self._mode == HVAC_MODE_COOL:
            if current > window_threshold:
                self._logger.warning(
                    "temperature rise %s: open window detected, maintain old control value",
                    round(current, 5),
                )
                return True

    def set_pid_param(
        self, hvac_data, kp=None, ki=None, kd=None, update=False
    ):  # pylint: disable=invalid-name
        """Set PID parameters."""
        hvac_data = self.get_hvac_data(hvac_data)
        if kp is not None:
            hvac_data[CONF_KP] = kp
        if ki is not None:
            hvac_data[CONF_KI] = ki
        if kd is not None:
            hvac_data[CONF_KD] = kd

        if update:
            hvac_data.PID["pidController"].set_pid_param(kp=kp, ki=ki, kd=kd)

    def pid_reset_time(self):
        """Reset the current time for PID to avoid overflow of the intergral part
        when switching between hvac modes"""
        if self.is_hvac_pid_mode:
            self._pid.PID["pidController"].reset_time()
        if self.is_hvac_valve_mode:
            self._master.PID["pidController"].reset_time()

    def set_integral(self, hvac_data, integral):
        """function to overwrite integral value"""
        hvac_data = self.get_hvac_data(hvac_data)
        hvac_data.PID["pidController"].integral = integral

    @property
    def get_ka_kb_param(self):
        """Return the wc parameters of the thermostat."""
        if self.is_hvac_wc_mode:
            ka = self._wc[CONF_KA]  # pylint: disable=invalid-name
            kb = self._wc[CONF_KB]  # pylint: disable=invalid-name
            return (ka, kb)
        else:
            return (None, None)

    @property
    def get_wc_sensor(self):
        """return the sensor entity"""
        if self.is_hvac_wc_mode:
            return self._wc[CONF_SENSOR_OUT]
        else:
            return None

    @property
    def get_satelites(self):
        """return the satelite thermostats"""
        if self.is_master_mode:
            return self._master[CONF_SATELITES]
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
        return self._master[CONF_GOAL]

    @goal.setter
    def goal(self, goal):
        """set setpoint for valve mode"""
        self._master[CONF_GOAL] = goal

    def update_satelite(self, name, mode, setpoint, current, area, valve):
        """set new state of a satelite"""
        self._logger.debug("new data for : %s", name)
        if None in [setpoint, current, area, valve]:
            self._satelites[name] = {
                "mode": None,
                "setpoint": 0,
                "current": 0,
                "area": 0,
                "valve_pos": 0,
            }
        else:
            self._satelites[name] = {
                "mode": mode,
                "setpoint": setpoint,
                "current": current,
                "area": area,
                "valve_pos": valve,
            }

        self.master_setpoint()
        self.master_current_temp()
        self.master_valve_position()

    def master_setpoint(self):
        """set setpoint based on satelites"""
        sum_area = 0
        sum_product = 0

        for _, data in self._satelites.items():
            if data["mode"] == self._mode:
                sum_area += data["area"]
                sum_product += data["area"] * data["setpoint"]
        if sum_area:
            self.target_temperature = round(sum_product / sum_area, 2)
        else:
            self.target_temperature = None

    def master_current_temp(self):
        """set current temperature by satelites"""
        sum_area = 0
        sum_product = 0

        for _, data in self._satelites.items():
            if data["mode"] == self._mode and data["current"]:
                sum_area += data["area"]
                sum_product += data["area"] * data["current"]
        if sum_area:
            self.current_temperature = round(sum_product / sum_area, 2)
        else:
            self.current_temperature = None

    def master_valve_position(self):
        """get maximal valve opening"""
        valve_pos = 0

        for _, data in self._satelites.items():
            if data["mode"] == self._mode and data["valve_pos"]:
                valve_pos = max(valve_pos, data["valve_pos"])
        if valve_pos == 0:
            if self.is_hvac_valve_mode:
                self._master.PID["pidController"].reset_time()
        self._master_max_valve_pos = valve_pos

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
    def is_hvac_pid_mode(self):
        """return the control mode"""
        if self._pid:
            return True
        else:
            return False

    @property
    def is_master_mode(self):
        """return the control mode"""
        if self._master:
            return True
        else:
            return False

    @property
    def is_hvac_valve_mode(self):
        """return the control mode"""
        if self.is_master_mode:
            if CONF_GOAL in self._master:
                return True
            else:
                return False
        else:
            return False

    @property
    def is_hvac_wc_mode(self):
        """return the control mode"""
        if self._wc:
            return True
        else:
            return False

    @property
    def is_satelite_allowed(self):
        """Return if satelite mode is allowed."""
        if self.is_hvac_proportional_mode and not self.is_master_mode:
            return True
        else:
            return False

    @property
    def is_hvac_switch_on_off(self):
        """check if on-off mode is active"""
        if self.is_hvac_on_off_mode or not self.get_pwm_mode.seconds == 0:
            return True
        else:
            return False

    @property
    def get_variable_attr(self):
        """return attributes for climate entity"""
        tmp_dict = {}
        tmp_dict["target_temp"] = self.target_temperature
        tmp_dict["satelite_allowed"] = self.is_satelite_allowed

        key = "control_output"

        if self.is_master_mode:
            tmp_dict["satelites"] = self.get_satelites
        if self.is_hvac_proportional_mode:
            tmp_dict["valve_pos"] = round(self.get_control_output, 3)
        if self.is_hvac_pid_mode:
            tmp_dict["PID_values"] = self.get_pid_param(self._pid)
            if self._pid.PID["pidController"]:
                tmp_dict["PID_P"] = round(
                    self._pid.PID["pidController"].p_var, 3
                )
                tmp_dict["PID_I"] = round(
                    self._pid.PID["pidController"].i_var, 3
                )
                tmp_dict["PID_D"] = round(
                    self._pid.PID["pidController"].d_var, 5
                )
            tmp_dict["PID_valve_pos"] = round(self._pid[key], 3)
        if self.is_hvac_valve_mode:
            tmp_dict["Valve_PID_values"] = self.get_pid_param(self._master)
            if self._master.PID["pidController"]:
                tmp_dict["Valve_PID_P"] = round(
                    self._master.PID["pidController"].p_var, 3
                )
                tmp_dict["Valve_PID_I"] = round(
                    self._master.PID["pidController"].i_var, 3
                )
                tmp_dict["Valve_PID_D"] = round(
                    self._master.PID["pidController"].d_var, 5
                )
            tmp_dict["Valve_PID_valve_pos"] = self._master[key]
        if self.is_hvac_wc_mode:
            tmp_dict["ab_values"] = self.get_ka_kb_param
            tmp_dict["wc_valve_pos"] = round(self._wc[key], 3)
        return tmp_dict

    def restore_reboot(self, data, restore_parameters, restore_integral):
        """restore attributes for climate entity"""
        self.target_temperature = data["target_temp"]

        if self.is_hvac_pid_mode:
            if restore_parameters and "PID_values" in data:
                kp, ki, kd = data["PID_values"]  # pylint: disable=invalid-name
                self.set_pid_param(self._pid, kp=kp, ki=ki, kd=kd, update=True)
        if self.is_hvac_valve_mode:
            if restore_parameters and "Valve_PID_values" in data:
                kp, ki, kd = data["Valve_PID_values"]  # pylint: disable=invalid-name
                self.set_pid_param(self._master, kp=kp, ki=ki, kd=kd, update=True)

        if restore_integral:
            if self.is_hvac_pid_mode:
                if "PID_integral" in data:
                    self._pid.PID["pidController"].integral = data["PID_integral"]
            if self.is_hvac_valve_mode:
                if "Valve_PID_integral" in data:
                    self._master.PID["pidController"].integral = data[
                        "Valve_PID_integral"
                    ]

        self.pid_reset_time()

def getRoundedThresholdv1(a, MinClip):
    '''https://stackoverflow.com/questions/7859147/round-in-numpy-to-nearest-step'''
    scaled = a/MinClip
    return np.where(scaled % 1 >= 0.5, np.ceil(scaled), np.floor(scaled))*MinClip