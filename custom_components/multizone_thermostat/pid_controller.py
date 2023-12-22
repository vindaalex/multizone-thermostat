"""module with PID controller.

Based on Arduino PID Library
See https://github.com/br3ttb/Arduino-PID-Library
"""
from datetime import datetime
import logging

from . import DOMAIN


class PIDController:
    """A proportional-integral-derivative controller."""

    def __init__(
        self,
        name: str,
        PID_type: str,  # pylint: disable=invalid-name
        master_mode: bool,
        sampletime: float,
        kp: float | None,  # pylint: disable=invalid-name
        ki: float | None,  # pylint: disable=invalid-name
        kd: float | None,  # pylint: disable=invalid-name
        time: datetime,
        out_min: float = float("-inf"),
        out_max: float = float("inf"),
    ) -> None:
        """Prepare the pid controller."""
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

        self._name = name + "." + PID_type
        self._logger = logging.getLogger(DOMAIN).getChild(self._name)

        self._Kp = kp  # pylint: disable=invalid-name
        self._Ki = ki  # pylint: disable=invalid-name
        self._Kd = kd  # pylint: disable=invalid-name
        self.p_var = 0
        self.i_var = 0
        self.d_var = 0
        self._sampletime = sampletime
        self._out_min = out_min
        self._out_max = out_max
        self._integral = 0
        self._differential = 0
        self._windupguard = 1
        self._last_input = 0
        # self._old_setpoint = None
        self._last_output = 0
        self._last_calc_timestamp = None
        self._time = time
        self.master_mode = master_mode

    def calc(self, input_val: float, setpoint, force: bool = False) -> float:
        """Calculate pid for given input_val and setpoint."""
        if not setpoint:
            self._logger.warning(
                "No setpoint specified, return with previous control value %s",
                self._last_output,
            )
            return self._last_output

        if not input_val:
            self._logger.warning(
                "no current value specified, return with previous control value %.2f",
                self._last_output,
            )
            return self._last_output

        now = self._time()
        time_diff = None
        if self._last_calc_timestamp is not None:
            time_diff = now - self._last_calc_timestamp

        # reset previous result in case filter mode changed the output
        # between temp only and temp + velocity
        if type(input_val) is not type(self._last_input):
            self._last_input = input_val

        # UKF temp + velocity
        if isinstance(input_val, list):
            current_temp, self._differential = input_val
            self._logger.debug(
                "current temp '%.2f'; velocity %.4f",
                current_temp,
                self._differential,
            )
        # when only current temp is provided
        else:
            current_temp = input_val
            if self._last_calc_timestamp is not None and time_diff is not None:
                input_diff = current_temp - self._last_input
                self._differential = input_diff / time_diff

        # Compute all the working error variables
        error = setpoint - current_temp

        self.calc_integral(error, time_diff)
        self.p_var = self._Kp * error
        self.i_var = self._Ki * self._integral
        self.d_var = self._Kd * self._differential

        # Compute PID Output
        self._last_output = self.p_var + self.i_var + self.d_var
        self._last_output = min(self._last_output, self._out_max)
        self._last_output = max(self._last_output, self._out_min)

        # Log some debug info
        self._logger.debug(
            "Contribution P: %.4f; I: %.4f; D: %.4f; Output: %.2f",
            self.p_var,
            self.i_var,
            self.d_var,
            self._last_output,
        )

        if not self.master_mode:
            # fully open if error is too high
            if (  # heating
                (
                    self._Kp > 0
                    and (
                        (error > 0 and error > self._out_max / self._Kp)
                        or (error > 1.5)
                    )
                )
                # cooling
                or (
                    self._Kp < 0
                    and (
                        (error < 0 and error < self._out_max / self._Kp)
                        or (error < -1.5)
                    )
                )
            ):
                # when temp is too low when heating or too high when cooling set fully open
                # similar as honeywell TPI
                self._logger.debug(
                    "setpoint %.2f, current temp %.2f: too low temp open to max: %.2f",
                    setpoint,
                    current_temp,
                    self._out_max,
                )
                self._last_output = self._out_max

            # fully close if error is too low
            elif (
                # heating
                (self._Kp > 0 and error < -1.5)
                or
                # cooling
                (self._Kp < 0 and error > 1.5)
            ):
                # when temp is too low when heating or too high when cooling set fully open
                # similar as honeywell TPI
                self._logger.debug(
                    "setpoint %.2f, current temp %.2f: too low temp open to max: %.2f",
                    setpoint,
                    current_temp,
                    self._out_max,
                )
                self._last_output = self._out_min

        # Remember some variables for next time
        self._last_input = input_val
        self._last_calc_timestamp = now
        return self._last_output

    def calc_integral(self, error: float, time_diff: datetime | None) -> float | None:
        """Calcualte integral.

        In order to prevent windup, only integrate if the process is not saturated
        """
        if time_diff is None or self._last_calc_timestamp is None:
            return

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

    def reset_time(self) -> None:
        """Reset time to void large intergrl buildup."""

        if self._last_calc_timestamp != 0:
            self._logger.debug("reset PID integral reference time")
            self._last_calc_timestamp = self._time()

    @property
    def integral(self) -> float:
        """Return integral."""
        return self._integral

    @integral.setter
    def integral(self, integral: float) -> None:
        """Set integral."""
        self._logger.info("Forcing new integral: %s", integral)
        self._integral = integral / self._Ki

    @property
    def differential(self) -> float:
        """Get differential."""
        return self._differential

    def set_pid_param(
        self, kp: float | None = None, ki: float | None = None, kd: float | None = None
    ):  # pylint: disable=invalid-name
        """Set PID parameters."""
        if kp is not None:
            self._Kp = kp
        if ki is not None:
            self._Ki = ki
        if kd is not None:
            self._Kd = kd

    @property
    def get_PID_parts(self) -> dict:
        """PID component."""
        return {"p": self.p_var, "i": self.i_var, "d": self.d_var}
