import math
import logging
import numpy as np

from time import time
from collections import deque, namedtuple


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
        PID_type,
        sampletime,
        kp,
        ki,
        kd,
        out_min=float("-inf"),
        out_max=float("inf"),
        time=time,
        window_open=None,
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
        if window_open:
            if window_open > 0:
                raise ValueError("window open should be less than 0")

        self._LOGGER = logging.getLogger(logger).getChild(PID_type)
        self._Kp = kp
        self._Ki = ki
        self._Kd = kd
        self._LOGGER.debug("_sampletime: {0}".format(sampletime))
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
        self._window_open = window_open

    def calc(self, input_val, setpoint, force=False):
        """Adjusts and holds the given setpoint.

        Args:
            input_val (float): The input value.
            setpoint (float): The target value.

        Returns:
            A value between `out_min` and `out_max`.
        """
        if not setpoint:
            self._LOGGER.warning(
                "no setpoint specified, return with previous control value {0}".format(
                    self._last_output
                )
            )
            return self._last_output

        now = self._time()
        if self._last_calc_timestamp != 0:
            if (now - self._last_calc_timestamp) < self._sampletime and not force:
                self._LOGGER.debug(
                    "pid timediff: {0} < sampletime {1}: keep previous value".format(
                        round((now - self._last_calc_timestamp), 0),
                        self._sampletime,
                    )
                )
                return self._last_output
            time_diff = now - self._last_calc_timestamp

        if type(input_val) != type(self._last_input):
            # reset previous result in case filter mode changed the output
            self._last_input = input_val

        if isinstance(input_val, (list, tuple, np.ndarray)):
            current_temp, self._differential = input_val
            self._LOGGER.debug(
                "current temp {0} ; current velocity {1}".format(
                    current_temp,
                    self._differential,
                )
            )
            if self._window_open:
                if self._differential < self._window_open / 3600:
                    self._LOGGER.warning(
                        "open window detected, maintain old control value"
                    )
                    return self._last_output
        else:
            # this is only triggered for master mode, velocity is less stable and open window check not required.
            if not input_val:
                self._LOGGER.warning(
                    "no current value specified, return with previous control value {0}".format(
                        self._last_output
                    )
                )
                return self._last_output
            current_temp = input_val
            if self._last_calc_timestamp != 0:
                input_diff = current_temp - self._last_input
                self._differential = input_diff / time_diff

        # Compute all the working error variables
        error = setpoint - current_temp

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

        p = self._Kp * error
        i = self._Ki * self._integral
        d = self._Kd * self._differential

        # Compute PID Output
        self._last_output = p + i + d
        self._last_output = min(self._last_output, self._out_max)
        self._last_output = max(self._last_output, self._out_min)

        # Log some debug info
        self._LOGGER.debug("P: {0}".format(p))
        self._LOGGER.debug("I: {0}".format(i))
        self._LOGGER.debug("D: {0}".format(d))
        self._LOGGER.debug("output: {0}".format(self._last_output))

        # Remember some variables for next time
        self._last_input = input_val
        self._last_calc_timestamp = now
        return self._last_output

    def reset_time(self):
        self._last_calc_timestamp = self._time()

    @property
    def integral(self):
        return self._integral

    @integral.setter
    def integral(self, integral):
        self._LOGGER.info("forcing new integral: {0}".format(integral))
        self._integral = integral

    @property
    def differential(self):
        return self._differential

    def set_pid_param(self, kp=None, ki=None, kd=None):
        """Set PID parameters."""
        if kp is not None:
            self._Kp = kp
        if ki is not None:
            self._Ki = ki
        if kd is not None:
            self._Kd = kd
