"""module to initiate UKF filter for temperature readings"""
import time

import numpy as np

from .UKF_filter.discretization import Q_discrete_white_noise
from .UKF_filter.sigma_points import MerweScaledSigmaPoints
from .UKF_filter.UKF import UnscentedKalmanFilter


class UKFFilter:
    """initiate the UKF filter for thermostat"""

    def __init__(self, current_temp, timedelta, filter_mode):
        """init Unscented kalman filter"""
        self._interval = 0
        self._last_update = time.time()
        self._mode = filter_mode
        sigmas = MerweScaledSigmaPoints(n=2, alpha=0.001, beta=2, kappa=0)
        self._kf_temp = UnscentedKalmanFilter(
            dim_x=2, dim_z=1, dt=timedelta, hx=hx, fx=fx, points=sigmas
        )
        self._kf_temp.x = np.array([float(current_temp), 0.0])
        self._kf_temp.P *= 0.2  # initial uncertainty
        self.interval = timedelta

    def kf_predict(self):
        """
        run UKF prediction with variable timestep
        https://github.com/rlabbe/filterpy/issues/196
        """
        timedelta = time.time() - self._last_update
        self._last_update = time.time()
        self._kf_temp.predict(dt=timedelta)

    def kf_update(self, current_temp):
        """run UKF update"""
        self._kf_temp.update(float(current_temp))

    @property
    def get_temp(self):
        """return filtered temperature"""
        return float(self._kf_temp.x[0])

    @property
    def get_vel(self):
        """return filtered velocity"""
        return float(self._kf_temp.x[1])

    def set_Q_R(self, timedelta=None):  # pylint: disable=invalid-name
        """process noise"""
        # default Q .002 R 4
        if timedelta:
            tmp_interval = timedelta
        else:
            tmp_interval = self._interval
        self._kf_temp.Q = Q_discrete_white_noise(
            dim=2,
            dt=tmp_interval,
            var=((0.01 / self.filter_mode) / (tmp_interval**1.2)) ** 2,
        )

        # measurement noise std**2
        self._kf_temp.R = np.diag(
            [(self.filter_mode * (1800 / tmp_interval) ** 0.8) ** 2]
        )

    @property
    def interval(self):
        """return time step"""
        return self._interval

    @interval.setter
    def interval(self, timedelta):  # pylint: disable=invalid-name
        """set time step"""
        if timedelta != self._interval:
            self._interval = timedelta
            self.set_Q_R()

    @property
    def filter_mode(self):
        """return current filter mode"""
        return self._mode

    def set_filter_mode(self, val, timedelta=None):
        """set current filter mode"""
        if val != self._mode:
            self._mode = val
            self.set_Q_R(timedelta=timedelta)


def fx(x, dt):  # pylint: disable=invalid-name
    # xout = np.empty_like(x)
    # xout[0] = x[1] * dt + x[0]
    # xout[1] = x[1]
    # return xout

    F = np.array([[1, dt], [0, 1]], dtype=float)  # pylint: disable=invalid-name
    # F = np.array([
    #     [1, dt,0.5*dt**2],
    #     [0, 1,dt],
    #     [0,0,1]], dtype=float)
    return np.dot(F, x)


def hx(x):  # pylint: disable=invalid-name
    return x[:1]  # return position [x]
