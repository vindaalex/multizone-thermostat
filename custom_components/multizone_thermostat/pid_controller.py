"""module with PID controller"""
# import math
import logging
import numpy as np

# from time import time
# from collections import namedtuple


# Based on Arduino PID Library
# See https://github.com/br3ttb/Arduino-PID-Library
class PIDController(object):
    """A proportional-integral-derivative controller.

    Args:
        sampletime (float): The interval between calc() calls.
        kp (float): Proportional coefficient.
        ki (float): Integral coefficient.
        kd (float): Derivative coefficient.
        out_min (float): Lower output limit.
        out_max (float): Upper output limit.
        time (function): A function which returns the current time in seconds.
    """

    def __init__(
        self,
        logger,
        PID_type,  # pylint: disable=invalid-name
        sampletime,
        kp,  # pylint: disable=invalid-name
        ki,  # pylint: disable=invalid-name
        kd,  # pylint: disable=invalid-name
        time,
        out_min=float("-inf"),
        out_max=float("inf"),
    ):
        if kp is None:
            raise ValueError("kp must be specified")
        if ki is None:
            raise ValueError("ki must be specified")
        if kd is None:
            raise ValueError("kd must be specified")
        if sampletime <= 0:
            raise ValueError("sampletime must be greater than 0")
        if out_min >= out_max:
            raise ValueError("out_min must be less than out_max")

        self._logger = logging.getLogger(logger).getChild(PID_type)
        self._Kp = kp  # pylint: disable=invalid-name
        self._Ki = ki  # pylint: disable=invalid-name
        self._Kd = kd  # pylint: disable=invalid-name
        self.p_var = 0
        self.i_var = 0
        self.d_var = 0
        self._logger.debug("_sampletime: %.2f", sampletime)
        self._sampletime = sampletime
        self._out_min = out_min
        self._out_max = out_max
        self._integral = 0
        self._differential = 0
        self._windupguard = 1
        self._last_input = 0
        self._last_output = 0
        self._last_calc_timestamp = 0
        self._time = time

    def calc(self, input_val, setpoint, force=False, master_mode=False):
        """Adjusts and holds the given setpoint.

        Args:
            input_val (float): The input value.
            setpoint (float): The target value.

        Returns:
            A value between `out_min` and `out_max`.
        """
        if not setpoint:
            self._logger.warning(
                "no setpoint specified, return with previous control value {0}".format(
                    self._last_output
                )
            )
            return self._last_output

        now = self._time()
        if self._last_calc_timestamp != 0:
            if (now - self._last_calc_timestamp) < self._sampletime and not force:
                self._logger.debug(
                    "pid timediff: %.0f < sampletime %.2f: keep previous value",
                    (now - self._last_calc_timestamp),
                    self._sampletime,
                )
                return self._last_output
            time_diff = now - self._last_calc_timestamp

        if type(input_val) is not type(self._last_input):
            # reset previous result in case filter mode changed the output
            self._last_input = input_val

        if isinstance(input_val, (list, tuple, np.ndarray)):
            current_temp, self._differential = input_val
            self._logger.debug(
                "current temp '%.2f'; velocity %.4f",
                current_temp,
                self._differential,
            )
        else:
            # this is only triggered for master mode, velocity is less stable.
            if not input_val:
                self._logger.warning(
                    "no current value specified, return with previous control value %.2f",
                    self._last_output,
                )
                return self._last_output
            current_temp = input_val
            if self._last_calc_timestamp != 0:
                input_diff = current_temp - self._last_input
                self._differential = input_diff / time_diff

        # Compute all the working error variables
        error = setpoint - current_temp

        if (
            (self._Kp > 0 and error > 0 and error > self._out_max / self._Kp)
            or (self._Kp < 0 and error < 0 and error < self._out_max / self._Kp)
        ) and not master_mode:
            # when temp is too low when heating or too high when cooling set fully open
            # similar as honeywell TPI
            self._logger.debug(
                "setpoint %.2f, current temp %.2f: too low temp open to max: %.2f",
                setpoint,
                current_temp,
                self._out_max,
            )
            self._last_output = self._out_max

        else:
            # In order to prevent windup, only integrate if the process is not saturated
            # if self._last_output < self._out_max and self._last_output > self._out_min:
            if self._last_calc_timestamp != 0:
                if self._Ki:
                    self._integral += time_diff * error
                    self._integral = min(
                        self._integral,
                        self._out_max / (self._windupguard * abs(self._Ki)),
                    )
                    self._integral = max(
                        self._integral,
                        self._out_min / (self._windupguard * abs(self._Ki)),
                    )

            self.p_var = self._Kp * error
            self.i_var = self._Ki * self._integral
            self.d_var = self._Kd * self._differential

            # Compute PID Output
            self._last_output = self.p_var + self.i_var + self.d_var
            self._last_output = min(self._last_output, self._out_max)
            self._last_output = max(self._last_output, self._out_min)

            # Log some debug info
            self._logger.debug("contribution P: %.4f", self.p_var)
            self._logger.debug("contribution I: %.4f", self.i_var)
            self._logger.debug("contribution D: %.4f", self.d_var)
            self._logger.debug("output: %.2f", self._last_output)

        # Remember some variables for next time
        self._last_input = input_val
        self._last_calc_timestamp = now
        return self._last_output

    def reset_time(self):
        """reset time to void large intergrl buildup"""
        self._last_calc_timestamp = self._time()

    @property
    def integral(self):
        """return integral"""
        return self._integral

    @integral.setter
    def integral(self, integral):
        """set integral"""
        self._logger.info("forcing new integral: {0}".format(integral))
        self._integral = integral

    @property
    def differential(self):
        """get differential"""
        return self._differential

    def set_pid_param(self, kp=None, ki=None, kd=None):  # pylint: disable=invalid-name
        """Set PID parameters."""
        if kp is not None:
            self._Kp = kp
        if ki is not None:
            self._Ki = ki
        if kd is not None:
            self._Kd = kd
