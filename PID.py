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
        derative_avg=None,
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
        self._averaging = derative_avg

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

        if isinstance(input_val, (list, tuple, np.ndarray)):
            current_temp, self._differential = input_val
            self._LOGGER.debug(
                "current temp {0} ; current velocity {1}".format(
                    current_temp,
                    self._differential,
                )
            )
            if self._differential < -0.001:
                self._LOGGER.warning("open window detected, maintain old control value")
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


# Based on a fork of Arduino PID AutoTune Library
# See https://github.com/t0mpr1c3/Arduino-PID-AutoTune-Library
class PIDAutotune(object):
    """Determines viable parameters for a PID controller.

    Args:
        setpoint (float): The target value.
        out_step (float): The value by which the output will be
            increased/decreased when stepping up/down.
        sampletime (float): The interval between run() calls.
        lookback (float): The reference period for local minima/maxima.
        out_min (float): Lower output limit.
        out_max (float): Upper output limit.
        noiseband (float): Determines by how much the input value must
            overshoot/undershoot the setpoint before the state changes.
        time (function): A function which returns the current time in seconds.
    """

    PIDParams = namedtuple("PIDParams", ["Kp", "Ki", "Kd"])

    PEAK_AMPLITUDE_TOLERANCE = 0.05  # or 1.03?
    STATE_OFF = "off"
    STATE_RELAY_STEP_UP = "relay step up"
    STATE_RELAY_STEP_DOWN = "relay step down"
    STATE_SUCCEEDED = "succeeded"
    STATE_FAILED = "failed"

    _tuning_rules = {
        # rule: [Kp_divisor, Ki_divisor, Kd_divisor]
        "ziegler-nichols": [34, 40, 160],
        "tyreus-luyben": [44, 9, 126],
        "ciancone-marlin": [66, 88, 162],
        "pessen-integral": [28, 50, 133],
        "some-overshoot": [60, 40, 60],
        "no-overshoot": [100, 40, 60],
        "brewing": [2.5, 6, 380],
    }

    def __init__(
        self,
        logger,
        PID_type,
        setpoint,
        out_step=10,
        sampletime=5,
        lookback=60,
        out_min=float("-inf"),
        out_max=float("inf"),
        noiseband=0.5,
        time=time,
    ):
        if setpoint is None:
            raise ValueError("setpoint must be specified")
        if out_step < 1:
            raise ValueError("out_step must be greater or equal to 1")
        if sampletime < 1:
            raise ValueError("sampletime must be greater or equal to 1")
        if lookback < sampletime:
            raise ValueError("lookback must be greater or equal to sampletime")
        if out_min >= out_max:
            raise ValueError("out_min must be less than out_max")

        self._time = time
        self._LOGGER = logging.getLogger(logger).getChild(PID_type)
        self._inputs = deque(maxlen=round(lookback / sampletime))
        self._sampletime = sampletime
        self._setpoint = setpoint
        self._outputstep = out_step
        self._noiseband = noiseband
        self._out_min = out_min
        self._out_max = out_max
        self._state = PIDAutotune.STATE_OFF
        self._peak_timestamps = deque(maxlen=5)
        self._peaks = deque(maxlen=5)
        self._output = 0
        self._last_run_timestamp = 0
        self._peak_type = 0
        self._peak_count = 0
        self._initial_output = 0
        self._induced_amplitude = 0
        self._Ku = 0
        self._Pu = 0
        self._consolidating_max = 0
        self._consolidating_min = 0
        self._consolidating_max_timestamp = 0
        self._consolidating_min_timestamp = 0

    @property
    def state(self):
        """Get the current state."""
        return self._state

    @property
    def output(self):
        """Get the last output value."""
        return self._output

    @property
    def setpoint(self):
        """Get the setpoint value."""
        return self._setpoint

    @property
    def tuning_rules(self):
        """Get a list of all available tuning rules."""
        return self._tuning_rules.keys()

    def get_pid_parameters(
        self,
        tuning_rule="ziegler-nichols",
        use_tuning_rules=True,
        autotune_control_type="classic_pid",
    ):
        """Get PID parameters.

        Args:
            tuning_rule (str): Sets the rule which should be used to calculate
                the parameters.
            use_tuning_rules (boolean): Set to true to use the tuning rules and
                false to use Ziegler–Nichols' method.
            autotune_control_type (str): Sets the Ziegler Nichols control type
                according to:
                https://en.wikipedia.org/wiki/Ziegler%E2%80%93Nichols_method
                Values: p, pi, pd, classic_pid, pessen_integral_rule,
                    some_overshoot, no_overshoot

        """
        if self._state == PIDAutotune.STATE_FAILED:
            return None
        else:
            # https://en.wikipedia.org/wiki/Ziegler%E2%80%93Nichols_method
            self._LOGGER.debug("Ultimate gain:")
            self._LOGGER.debug("Ku value: {0}".format(self._Ku))
            self._LOGGER.debug("Oscilation period:")
            self._LOGGER.debug("Pu value: {0}".format(self._Pu))

            self._LOGGER.debug("Ziegler–Nichols P control type:")
            kp = 0.5 * self._Ku
            ki = kd = 0
            self._LOGGER.debug("Kp value: {0}".format(kp))

            self._LOGGER.debug("Ziegler–Nichols PI control type:")
            kp = 0.45 * self._Ku
            ti = self._Pu / 1.2
            ki = 0.54 * self._Ku / self._Pu
            self._LOGGER.debug("Kp value: {0}".format(kp))
            self._LOGGER.debug("Ti value: {0}".format(ti))
            self._LOGGER.debug("Ki value: {0}".format(ki))

            self._LOGGER.debug("Ziegler–Nichols PD control type:")
            kp = 0.8 * self._Ku
            td = self._Pu / 8
            kd = self._Ku * self._Pu / 10
            self._LOGGER.debug("Kp value: {0}".format(kp))
            self._LOGGER.debug("Td value: {0}".format(td))
            self._LOGGER.debug("Kd value: {0}".format(kd))

            self._LOGGER.debug("Ziegler–Nichols classic PID control type:")
            kp = 0.6 * self._Ku
            ti = self._Pu / 2
            td = self._Pu / 8
            ki = 1.2 * self._Ku / self._Pu
            kd = 3 * self._Ku * self._Pu / 40
            self._LOGGER.debug("Kp value: {0}".format(kp))
            self._LOGGER.debug("Ti value: {0}".format(ti))
            self._LOGGER.debug("Td value: {0}".format(td))
            self._LOGGER.debug("Ki value: {0}".format(ki))
            self._LOGGER.debug("Kd value: {0}".format(kd))

            self._LOGGER.debug("Ziegler–Nichols Pessen Integral Rule control type:")
            kp = 7 * self._Ku / 10
            ti = 2 * self._Pu / 5
            td = 3 * self._Pu / 20
            ki = 1.75 * self._Ku / self._Pu
            kd = 21 * self._Ku * self._Pu / 200
            self._LOGGER.debug("Kp value: {0}".format(kp))
            self._LOGGER.debug("Ti value: {0}".format(ti))
            self._LOGGER.debug("Td value: {0}".format(td))
            self._LOGGER.debug("Ki value: {0}".format(ki))
            self._LOGGER.debug("Kd value: {0}".format(kd))

            self._LOGGER.debug("Ziegler–Nichols Some overshoot control type:")
            kp = self._Ku / 3
            ti = self._Pu / 2
            td = self._Pu / 3
            ki = 0.666 * self._Ku / self._Pu
            kd = self._Ku * self._Pu / 9
            self._LOGGER.debug("Kp value: {0}".format(kp))
            self._LOGGER.debug("Ti value: {0}".format(ti))
            self._LOGGER.debug("Td value: {0}".format(td))
            self._LOGGER.debug("Ki value: {0}".format(ki))
            self._LOGGER.debug("Kd value: {0}".format(kd))

            self._LOGGER.debug("Ziegler–Nichols No overshoot control type:")
            kp = self._Ku / 5
            ti = self._Pu / 2
            td = self._Pu / 3
            ki = (2 / 5) * self._Ku / self._Pu
            kd = self._Ku * self._Pu / 15
            self._LOGGER.debug("Kp value: {0}".format(kp))
            self._LOGGER.debug("Ti value: {0}".format(ti))
            self._LOGGER.debug("Td value: {0}".format(td))
            self._LOGGER.debug("Ki value: {0}".format(ki))
            self._LOGGER.debug("Kd value: {0}".format(kd))

            if use_tuning_rules == True:
                divisors = self._tuning_rules[tuning_rule]
                kp = self._Ku / divisors[0]
                ki = kp / (self._Pu / divisors[1])
                kd = kp * (self._Pu / divisors[2])
                return PIDAutotune.PIDParams(kp, ki, kd)
            elif use_tuning_rules == False:
                if autotune_control_type == "p":
                    kp = 0.5 * self._Ku
                    ki = kd = 0
                    return PIDAutotune.PIDParams(kp, ki, kd)
                elif autotune_control_type == "pi":
                    kp = 0.45 * self._Ku
                    ki = 0.54 * self._Ku / self._Pu
                    kd = 0
                    return PIDAutotune.PIDParams(kp, ki, kd)
                elif autotune_control_type == "pd":
                    kp = 0.8 * self._Ku
                    ki = 0
                    kd = self._Ku * self._Pu / 10
                    return PIDAutotune.PIDParams(kp, ki, kd)
                elif autotune_control_type == "classic_pid":
                    kp = 0.6 * self._Ku
                    ki = 1.2 * self._Ku / self._Pu
                    kd = 3 * self._Ku * self._Pu / 40
                    return PIDAutotune.PIDParams(kp, ki, kd)
                elif autotune_control_type == "pessen_integral_rule":
                    kp = 7 * self._Ku / 10
                    ki = 1.75 * self._Ku / self._Pu
                    kd = 21 * self._Ku * self._Pu / 200
                    return PIDAutotune.PIDParams(kp, ki, kd)
                elif autotune_control_type == "some_overshoot":
                    kp = self._Ku / 3
                    ki = 0.666 * self._Ku / self._Pu
                    kd = self._Ku * self._Pu / 9
                    return PIDAutotune.PIDParams(kp, ki, kd)
                elif autotune_control_type == "no_overshoot":
                    kp = self._Ku / 5
                    ki = (2 / 5) * self._Ku / self._Pu
                    kd = self._Ku * self._Pu / 15
                    return PIDAutotune.PIDParams(kp, ki, kd)

    def reset_time(self):
        self._last_calc_timestamp = self._time()

    def run(self, input_val):
        """To autotune a system, this method must be called periodically.

        Args:
            input_val (float): The input value.

        Returns:
            `true` if tuning is finished, otherwise `false`.
        """
        now = self._time()

        if (
            self._state == PIDAutotune.STATE_OFF
            or self._state == PIDAutotune.STATE_SUCCEEDED
            or self._state == PIDAutotune.STATE_FAILED
        ):
            self._initTuner(input_val, now)
        elif (now - self._last_run_timestamp) < self._sampletime:
            return False

        self._last_run_timestamp = now
        try:
            # check input and change relay state if necessary
            if (
                self._state == PIDAutotune.STATE_RELAY_STEP_UP
                and input_val > self._setpoint + self._noiseband
            ):
                self._state = PIDAutotune.STATE_RELAY_STEP_DOWN
                self._LOGGER.debug("switched state: {0}".format(self._state))
                self._LOGGER.debug("input: {0}".format(input_val))
            elif (
                self._state == PIDAutotune.STATE_RELAY_STEP_DOWN
                and input_val < self._setpoint - self._noiseband
            ):
                self._state = PIDAutotune.STATE_RELAY_STEP_UP
                self._LOGGER.debug("switched state: {0}".format(self._state))
                self._LOGGER.debug("input: {0}".format(input_val))

            # set output
            if self._state == PIDAutotune.STATE_RELAY_STEP_UP:
                self._output = self._initial_output + self._outputstep
            elif self._state == PIDAutotune.STATE_RELAY_STEP_DOWN:
                self._output = self._initial_output - self._outputstep

            # respect output limits
            self._output = min(self._output, self._out_max)
            self._output = max(self._output, self._out_min)

            # identify peaks
            is_max = True
            is_min = True

            for val in self._inputs:
                is_max = is_max and (input_val >= val)
                is_min = is_min and (input_val <= val)

            if is_max:
                self._consolidating_max = input_val
                self._consolidating_max_timestamp = now
            elif is_min:
                self._consolidating_min = input_val
                self._consolidating_min_timestamp = now
            self._inputs.append(input_val)

            # we don't want to trust the maxes or mins until the input array is full
            if len(self._inputs) < self._inputs.maxlen:
                return False

            # increment peak count and record peak time for maxima and minima
            inflection = False

            # peak types:
            # -1: minimum
            # +1: maximum
            if is_max:
                if self._peak_type == -1:
                    inflection = True
                self._peak_type = 1
            elif is_min:
                if self._peak_type == 1:
                    inflection = True
                self._peak_type = -1

            # update peak times and values
            if inflection:
                self._peak_count += 1

                consolidated_peak = 0
                consolidated_timestamp = 0
                if self._state == PIDAutotune.STATE_RELAY_STEP_DOWN:
                    consolidated_peak = self._consolidating_max
                    consolidated_timestamp = self._consolidating_max_timestamp
                elif self._state == PIDAutotune.STATE_RELAY_STEP_UP:
                    consolidated_peak = self._consolidating_min
                    consolidated_timestamp = self._consolidating_min_timestamp

                self._peaks.append(consolidated_peak)
                self._peak_timestamps.append(consolidated_timestamp)
                self._LOGGER.debug("found peak: {0}".format(consolidated_peak))
                self._LOGGER.debug("timestamp: {0}".format(consolidated_timestamp))
                self._LOGGER.debug("peak count: {0}".format(self._peak_count))
                self._consolidating_max = 0
                self._consolidating_min = 0
                self._consolidating_max_timestamp = 0
                self._consolidating_min_timestamp = 0

            # check for convergence of induced oscillation
            # convergence of amplitude assessed on last 4 peaks (1.5 cycles)
            self._induced_amplitude = 0

            if inflection and (self._peak_count > 4):
                for i in range(0, len(self._peaks) - 2):
                    self._induced_amplitude += abs(self._peaks[i] - self._peaks[i + 1])

                if self._induced_amplitude == 0:
                    self._LOGGER.warning("induced amplitude = 0, wait for next peak")
                    return False
                self._induced_amplitude /= 3.0

                if (self._peak_count % 2) != 0:
                    abs_max = max(self._peaks[0], self._peaks[2])
                    abs_min = min(self._peaks[1], self._peaks[3])
                else:
                    abs_max = max(self._peaks[1], self._peaks[3])
                    abs_min = min(self._peaks[0], self._peaks[2])

                # check convergence criterion for amplitude of induced oscillation
                amplitude_dev = (abs_max - abs_min) / self._induced_amplitude

                self._LOGGER.debug("amplitude: {0}".format(self._induced_amplitude))
                self._LOGGER.debug("amplitude deviation: {0}".format(amplitude_dev))

                if amplitude_dev < PIDAutotune.PEAK_AMPLITUDE_TOLERANCE:
                    self._state = PIDAutotune.STATE_SUCCEEDED

            # if the autotune has not already converged
            # terminate after 10 cycles
            if self._peak_count >= 20:
                self._output = 0
                self._LOGGER.warning("autotune failed")
                self._state = PIDAutotune.STATE_FAILED
                return True

            if self._state == PIDAutotune.STATE_SUCCEEDED:
                self._LOGGER.warning("autotune succesful")
                self._output = 0

                # calculate ultimate gain
                self._Ku = (
                    4.0
                    * self._outputstep
                    / math.pi
                    / math.sqrt(self._induced_amplitude ** 2 - self._noiseband ** 2)
                )

                # calculate ultimate period in seconds
                period1 = self._peak_timestamps[3] - self._peak_timestamps[1]
                period2 = self._peak_timestamps[4] - self._peak_timestamps[2]
                self._Pu = 0.5 * (period1 + period2)
                return True
            return False
        except:
            self._output = 0
            self._LOGGER.warning("autotune failed")
            self._state = PIDAutotune.STATE_FAILED
            return True

    def _initTuner(self, inputValue, timestamp):
        self._peak_type = 0
        self._peak_count = 0
        self._output = 0
        self._initial_output = 0
        self._Ku = 0
        self._Pu = 0
        self._inputs.clear()
        self._peaks.clear()
        self._peak_timestamps.clear()
        self._peak_timestamps.append(timestamp)
        self._state = PIDAutotune.STATE_RELAY_STEP_UP
