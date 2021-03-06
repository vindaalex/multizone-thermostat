import filterpy
import numpy as np
import time
from filterpy.kalman import UnscentedKalmanFilter
from filterpy.common import Q_discrete_white_noise
from filterpy.kalman import JulierSigmaPoints, MerweScaledSigmaPoints
from filterpy.common import Q_continuous_white_noise


class filterr:
    def __init__(self, current_temp, dt):
        """Unscented kalman filter"""
        self._interval = 0
        self._last_update = time.time()
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
        self._kf_temp.Q = Q_discrete_white_noise(
            dim=2, dt=self._interval, var=(0.005 / (self._interval ** 1.2)) ** 2
        )

        """measurement noise std**2 """
        self._kf_temp.R = np.diag([(4 * (1800 / self._interval) ** 0.8) ** 2])

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, dt):
        if dt != self.interval:
            self._interval = dt
            self.set_Q_R()


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
