import numpy as np
import time
from UKF_filter.UKF import UnscentedKalmanFilter
from UKF_filter.discretization import Q_discrete_white_noise, Q_continuous_white_noise
from UKF_filter.sigma_points import JulierSigmaPoints, MerweScaledSigmaPoints
class filterr:
    def __init__(self, current_temp, dt, filter_mode):
        """Unscented kalman filter"""
        self._interval = 0
        self._last_update = time.time()
        self._mode = filter_mode
        # sigmas = JulierSigmaPoints(n=2, kappa=0)
        sigmas = MerweScaledSigmaPoints(n=2, alpha=0.001, beta=2, kappa=0)
        self._kf_temp = UnscentedKalmanFilter(
            dim_x=2, dim_z=1, dt=dt, hx=hx, fx=fx, points=sigmas
        )
        self._kf_temp.x = np.array([float(current_temp), 0.0])
        self._kf_temp.P *= 0.2  # initial uncertainty
        self.interval = dt

    def kf_predict(self):
        dt = time.time() - self._last_update
        self._last_update = time.time()
        self._kf_temp.predict(dt=dt)

    def kf_update(self, current_temp):
        self._kf_temp.update(float(current_temp))

    @property
    def get_temp(self):
        return float(self._kf_temp.x[0])

    @property
    def get_vel(self):
        return float(self._kf_temp.x[1])

    def set_Q_R(self, dt=None):
        """ process noise """
        # default Q .002 R 4
        if dt:
            tmp_interval = dt
        else:
            tmp_interval = self._interval
        self._kf_temp.Q = Q_discrete_white_noise(
            dim=2,
            dt=tmp_interval,
            var=((0.01 / self.filter_mode) / (tmp_interval ** 1.2)) ** 2,
        )

        """measurement noise std**2 """
        self._kf_temp.R = np.diag(
            [(self.filter_mode * (1800 / tmp_interval) ** 0.8) ** 2]
        )

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, dt):
        if dt != self._interval:
            self._interval = dt
            self.set_Q_R()

    @property
    def filter_mode(self):
        return self._mode

    def set_filter_mode(self, val, dt=None):
        if val != self._mode:
            self._mode = val
            self.set_Q_R(dt=dt)


def fx(x, dt):
    # xout = np.empty_like(x)
    # xout[0] = x[1] * dt + x[0]
    # xout[1] = x[1]
    # return xout

    F = np.array([[1, dt], [0, 1]], dtype=float)
    # F = np.array([
    #     [1, dt,0.5*dt**2],
    #     [0, 1,dt],
    #     [0,0,1]], dtype=float)
    return np.dot(F, x)


def hx(x):
    return x[:1]  # return position [x]
