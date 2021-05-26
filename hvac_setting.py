from . import PID as pid_controller
import time
import logging
import numpy as np


from homeassistant.components.climate.const import (
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    PRESET_AWAY,
    PRESET_NONE,
    SUPPORT_PRESET_MODE,
)

from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ENTITY_ID,
)

CONF_HVAC_MODE_INIT_TEMP = "initial_target_temp"
CONF_HVAC_MODE_MIN_TEMP = "min_temp"
CONF_HVAC_MODE_MAX_TEMP = "max_temp"
CONF_AWAY_TEMP = "away_temp"

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
CONF_SENSOR_FILTER = "sensor_filter"
CONF_CONTROL_REFRESH_INTERVAL = "control_interval"
CONF_DIFFERENCE = "difference"
CONF_MIN_DIFFERENCE = "min_difference"
CONF_MAX_DIFFERENCE = "max_difference"
CONF_MIN_DIFF = "minimal_diff"

# proportional valve control (pid/pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

# PWM/PID controller
CONF_PID_MODE = "PID_mode"
CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_D_AVG = "derative_avg"

CONF_AUTOTUNE = "autotune"
CONF_AUTOTUNE_CONTROL_TYPE = "autotune_control_type"
CONF_NOISEBAND = "noiseband"
CONF_AUTOTUNE_LOOKBACK = "autotune_lookback"
CONF_AUTOTUNE_STEP_SIZE = "tune_step_size"

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
CONF_GOAL = "goal"

SUPPORTED_PRESET_MODES = [
    PRESET_NONE,
    PRESET_AWAY,
    PRESET_PID_AUTOTUNE,
    PRESET_VALVE_AUTOTUNE,
]


class HVAC_Setting:
    def __init__(self, log_id, mode, conf):
        self._LOGGER = logging.getLogger(log_id).getChild(mode)
        self._LOGGER.info("Config hvac settings for mode : %s", mode)

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

        self._stuck_loop = False

        self._on_off = self._hvac_settings.get(CONF_ON_OFF_MODE)
        self._proportional = self._hvac_settings.get(CONF_PROPORTIONAL_MODE)

        if self.is_hvac_proportional_mode:
            self._wc = self._proportional.get(CONF_WC_MODE)
            self._pid = self._proportional.get(CONF_PID_MODE)
            self._master = self._proportional.get(CONF_MASTER_MODE)

        self.init_mode()

    def init_mode(self):
        """ init the defined control modes """
        if self.is_hvac_on_off_mode:
            self._LOGGER.debug("HVAC mode 'on_off' active")
            self.start_on_off()
        if self.is_hvac_proportional_mode:
            self._LOGGER.debug("HVAC mode 'proportional' active")
            if self.is_master_mode:
                self._LOGGER.debug("HVAC mode 'master' active")
                self.start_master()
                self._master_max_valve_pos = 0
                if self.is_hvac_valve_mode:
                    self._LOGGER.debug("HVAC mode 'valve control' active")
                    self.start_pid(self._master)
                    self._master["control_output"] = 0
            if self.is_hvac_pid_mode:
                self._LOGGER.debug("HVAC mode 'pid' active")
                self.start_pid(self._pid)
                self._pid["control_output"] = 0
            if self.is_hvac_wc_mode:
                self._LOGGER.debug("HVAC mode 'weather control' active")
                self._wc["control_output"] = 0

    def calculate(self, force=None):
        """Calculate the current control values for all activated modes"""
        if self.is_hvac_pid_mode or self.is_hvac_valve_mode:
            self.run_pid(force)
        if self.is_hvac_wc_mode:
            self.run_wc()

    @property
    def min_target_temp(self):
        """ return minimum target temperature"""
        if self.is_hvac_pid_mode and self.is_pid_autotune_active:
            return self._pid.PID["pidAutotune"].setpoint
        elif self.is_hvac_valve_mode and self.is_valve_autotune_active:
            return self._master.PID["pidAutotune"].setpoint
        else:
            return self._hvac_settings[CONF_HVAC_MODE_MIN_TEMP]

    @property
    def max_target_temp(self):
        """ return maximum target temperature"""
        if self.is_hvac_pid_mode and self.is_pid_autotune_active:
            return self._pid.PID["pidAutotune"].setpoint
        elif self.is_hvac_valve_mode and self.is_valve_autotune_active:
            return self._master.PID["pidAutotune"].setpoint
        else:
            return self._hvac_settings[CONF_HVAC_MODE_MAX_TEMP]

    def start_on_off(self):
        """set basic settings for hysteris mode"""
        self._LOGGER.debug("Init on_off settings for mode : %s", self._mode)
        try:
            self._on_off[CONF_KEEP_ALIVE]
        except:
            self._on_off[CONF_KEEP_ALIVE] = None

    def start_master(self):
        """Init the master mode"""
        self._satelites = {}
        self._master_setpoint = 0

    def start_pid(self, hvac_data):
        """Init the PID controller"""
        self._LOGGER.debug("Init pid settings for mode : %s", self._mode)
        hvac_data = self.get_hvac_data(hvac_data)
        hvac_data.PID = {}
        hvac_data.PID["_autotune_state"] = False
        hvac_data.PID["pidAutotune"] = None

        if CONF_GOAL in hvac_data:
            mode = "valve_pid"
        else:
            mode = "pid"

        min_diff, max_diff = self.get_difference_limits(hvac_data)
        kp, ki, kd = self.get_pid_param(hvac_data)
        min_cycle_duration = self.get_operate_cycle_time
        derative_avg = self.get_average

        hvac_data.PID["pidController"] = pid_controller.PIDController(
            self._LOGGER.name,
            mode,
            min_cycle_duration.seconds,
            kp,
            ki,
            kd,
            min_diff,
            max_diff,
            time.time,
            derative_avg,
        )

        hvac_data["control_output"] = 0

    def start_autotune(self, mode):
        """Init the autotune"""
        self._LOGGER.debug("Init autotune settings for mode : %s", mode)
        hvac_data = self.get_hvac_data(mode)
        if mode == "pid":
            setpoint = self.target_temperature
        elif mode == "valve":
            setpoint = self.goal
        else:
            self._LOGGER.error("Init autotune failed, no autotune is present: %s", mode)

        hvac_data.PID["_autotune_state"] = True
        hvac_data.PID["pidController"] = None

        min_cycle_duration = self.get_operate_cycle_time
        step_size = hvac_data[CONF_AUTOTUNE_STEP_SIZE]
        noiseband = hvac_data[CONF_NOISEBAND]
        autotune_lookback = hvac_data[CONF_AUTOTUNE_LOOKBACK]
        min_diff, max_diff = self.get_difference_limits(hvac_data)
        hvac_data.PID["pidAutotune"] = pid_controller.PIDAutotune(
            self._LOGGER.name,
            mode,
            setpoint,
            step_size,
            min_cycle_duration.seconds,
            autotune_lookback.seconds,
            min_diff,
            max_diff,
            noiseband,
            time.time,
        )
        self._LOGGER.warning(
            "Autotune will run with the current Setpoint %s Value you set. "
            "Changes, submited after, doesn't have any effect until it's finished.",
            setpoint,
        )

    def run_wc(self):
        """calcuate weather compension mode"""
        KA, KB = self.get_ka_kb_param
        hvac_data = self.get_hvac_data("wc")
        _, max_diff = self.get_difference_limits(hvac_data)

        if self.outdoor_temperature:
            temp_diff = self.target_temperature - self.outdoor_temperature
            self._wc["control_output"] = min(max(0, temp_diff * KA + KB), max_diff)
        else:
            self._LOGGER.warning("no outdoor temperature; continue with previous data")

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
                    current_temp = current[0]
                else:
                    current = self.current_temperature
                    current_temp = current
                setpoint = self.target_temperature
            if hvac_data.PID["_autotune_state"]:
                self._LOGGER.debug("Autotune mode")
                autotune = hvac_data[CONF_AUTOTUNE]
                autotune_control_type = hvac_data[CONF_AUTOTUNE_CONTROL_TYPE]
                cycle_time = self.get_operate_cycle_time
                min_diff, max_diff = self.get_difference_limits(hvac_data)
                if hvac_data.PID["pidAutotune"].run(current[0]):
                    if autotune_control_type == "none":
                        params = hvac_data.PID["pidAutotune"].get_pid_parameters(
                            autotune, True
                        )
                    else:
                        params = hvac_data.PID["pidAutotune"].get_pid_parameters(
                            autotune, False, autotune_control_type
                        )
                    if params:
                        kp = params.Kp
                        ki = params.Ki
                        kd = params.Kd
                        self.set_pid_param(hvac_data, kp=kp, ki=ki, kd=kd)

                        self._LOGGER.warning(
                            "Set Kp, Ki, Kd. "
                            "Smart thermostat now runs on autotune PID Controller: %s,  %s,  %s",
                            kp,
                            ki,
                            kd,
                        )
                    else:
                        self._LOGGER.warning(
                            "autotune has failed, continue with default values"
                        )
                    if CONF_GOAL in hvac_data:
                        mode = "valve_pid"
                    else:
                        mode = "pid"

                    hvac_data.PID["pidController"] = pid_controller.PIDController(
                        self._LOGGER.name,
                        mode,
                        cycle_time.seconds,
                        kp,
                        ki,
                        kd,
                        min_diff,
                        max_diff,
                        time.time,
                    )
                    hvac_data.PID["_autotune_state"] = False

                hvac_data["control_output"] = hvac_data.PID["pidAutotune"].output
            else:
                if CONF_SATELITES in hvac_data and current_temp == 0:
                    hvac_data["control_output"] = 0
                else:
                    hvac_data["control_output"] = hvac_data.PID["pidController"].calc(
                        current,
                        setpoint,
                        force,
                    )

    @property
    def get_control_output(self):
        """Return the control output of the thermostat."""
        key = "control_output"
        control_output = 0
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

        return round(control_output, 3)

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
        elif self._preset_mode == PRESET_PID_AUTOTUNE:
            self.start_autotune("pid")
        elif self._preset_mode == PRESET_VALVE_AUTOTUNE:
            self.start_autotune("valve")
        else:
            if self.is_hvac_pid_mode and self.is_pid_autotune_active:
                self.start_pid("pid")
            elif self.is_hvac_valve_mode and self.is_valve_autotune_active:
                self.start_pid("valve")

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
        return self._stuck_loop

    @stuck_loop.setter
    def stuck_loop(self, val):
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
            return self._proportional[CONF_PWM].seconds
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
            return None

    def get_average(self, hvac_data):
        """get averaging time for derative"""
        hvac_data = self.get_hvac_data(hvac_data)
        return hvac_data[CONF_D_AVG]

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
        """ get the controller data """
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

    def is_autotune_present(self, hvac_data=None):
        """Return if pid autotune is included."""

        def check_data(data_set):
            if CONF_AUTOTUNE in data_set:
                autotune = data_set[CONF_AUTOTUNE]
                if autotune != "none":
                    return True

        hvac_data = self.get_hvac_data(hvac_data)
        if hvac_data:
            if check_data(hvac_data):
                return True
        else:
            if self.is_hvac_pid_mode:
                if check_data(self._pid):
                    return True
            if self.is_hvac_valve_mode:
                if check_data(self._master):
                    return True

        return False

    @property
    def is_pid_autotune_active(self):
        """Return if pid autotune is running."""
        if self._pid:
            if CONF_AUTOTUNE in self._pid:
                hvac_data = self._pid
                if hvac_data.PID["_autotune_state"]:
                    return True

        return False

    @property
    def is_valve_autotune_active(self):
        """Return if valve autotune is running."""
        if self._master:
            if CONF_AUTOTUNE in self._master:
                hvac_data = self._master
                if hvac_data.PID["_autotune_state"]:
                    return True

        return False

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
        kp = None
        ki = None
        kd = None

        if CONF_KP in hvac_data:
            kp = hvac_data[CONF_KP]
        if CONF_KI in hvac_data:
            ki = hvac_data[CONF_KI]
        if CONF_KD in hvac_data:
            kd = hvac_data[CONF_KD]
        return (kp, ki, kd)

    @property
    def filter_mode(self):
        """Return the UKF mode."""
        if self.is_hvac_proportional_mode:
            return self._proportional[CONF_SENSOR_FILTER]
        else:
            return 0

    @filter_mode.setter
    def filter_mode(self, mode):
        """Set the UKF mode."""
        if self.is_hvac_proportional_mode:
            self._proportional[CONF_SENSOR_FILTER] = mode
        else:
            self._LOGGER.error("not filter supported for on-off control")
            return

    def set_pid_param(self, hvac_data, kp=None, ki=None, kd=None, update=False):
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

    @property
    def pid_reset_time(self):
        """Reset the current time for PID to avoid overflow of the intergral part
        when switching between hvac modes"""
        if self.is_hvac_pid_mode:
            if self.is_pid_autotune_active:
                self._pid.PID["pidAutotune"].reset_time()
            elif self._pid.PID["pidController"]:
                self._pid.PID["pidController"].reset_time()
        if self.is_hvac_valve_mode:
            if self.is_valve_autotune_active:
                self._master.PID["pidAutotune"].reset_time()
            elif self._master.PID["pidController"]:
                self._master.PID["pidController"].reset_time()

    def set_integral(self, hvac_data, integral):
        hvac_data = self.get_hvac_data(hvac_data)
        hvac_data.PID["pidController"].integral = integral

    @property
    def get_ka_kb_param(self):
        """Return the wc parameters of the thermostat."""
        if self.is_hvac_wc_mode:
            ka = self._wc[CONF_KA]
            kb = self._wc[CONF_KB]
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

    def set_ka_kb(self, ka=None, kb=None):
        """Set weather mode parameters."""

        if ka is not None:
            self._wc[CONF_KA] = ka
        if kb is not None:
            self._wc[CONF_KB] = kb

    @property
    def goal(self):
        """ get setpoint for valve mode """
        return self._master[CONF_GOAL]

    @goal.setter
    def goal(self, goal):
        """ set setpoint for valve mode """
        self._master[CONF_GOAL] = goal

    def update_satelite(self, name, mode, setpoint, current, area, valve):
        """set new state of a satelite"""
        self._LOGGER.debug("new data for : %s", name)
        self._satelites[name] = {
            "mode": mode,
            "setpoint": setpoint,
            "current": current,
            "area": area,
            "valve_pos": valve,
        }

        self.master_setpoint
        self.master_current_temp
        self.master_valve_position

    @property
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

    @property
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

    @property
    def master_valve_position(self):
        """get maximal valve opening"""
        valve_pos = 0

        for _, data in self._satelites.items():
            if data["mode"] == self._mode and data["valve_pos"]:
                valve_pos = max(valve_pos, data["valve_pos"])
        if valve_pos == 0:
            if self.is_hvac_valve_mode:
                if self.is_valve_autotune_active:
                    self._master.PID["pidAutotune"].reset_time()
                elif self._master.PID["pidController"]:
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
        """Return if pid autotune is running."""
        if self.is_hvac_proportional_mode and not self.is_master_mode:
            return True
        else:
            return False

    @property
    def is_hvac_switch_on_off(self):
        """check if on-off mode is active"""
        if self.is_hvac_on_off_mode or not self.get_pwm_mode == 0:
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
                tmp_dict["PID_integral"] = round(
                    self._pid.PID["pidController"].integral, 3
                )
                tmp_dict["PID_differential"] = round(
                    self._pid.PID["pidController"].differential, 5
                )
            tmp_dict["PID_valve_pos"] = round(self._pid[key], 3)
        if self.is_hvac_valve_mode:
            tmp_dict["Valve_PID_values"] = self.get_pid_param(self._master)
            if self._master.PID["pidController"]:
                tmp_dict["Valve_PID_integral"] = round(
                    self._master.PID["pidController"].integral, 3
                )
                tmp_dict["Valve_differential"] = round(
                    self._master.PID["pidController"].differential, 5
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
                kp, ki, kd = data["PID_values"]
                self.set_pid_param(self._pid, kp=kp, ki=ki, kd=kd, update=True)
        if self.is_hvac_valve_mode:
            if restore_parameters and "Valve_PID_values" in data:
                kp, ki, kd = data["Valve_PID_values"]
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

        self.pid_reset_time
