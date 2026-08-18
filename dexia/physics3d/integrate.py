"""Numerical integration + small math helpers (fixed-step, stable)."""

from __future__ import annotations

import math


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def wrap_angle(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def semi_implicit(pos, vel, acc, dt):
    """Symplectic (semi-implicit) Euler — stable for the reduced models.
    Returns (pos_next, vel_next) for numpy arrays."""
    vel_next = vel + acc * dt
    pos_next = pos + vel_next * dt
    return pos_next, vel_next


def rk4(f, y, dt):
    """Classic RK4 for an autonomous ODE dy/dt = f(y) (numpy state vector).
    Used where accuracy matters (ballistics / aero)."""
    k1 = f(y)
    k2 = f(y + 0.5 * dt * k1)
    k3 = f(y + 0.5 * dt * k2)
    k4 = f(y + dt * k3)
    return y + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
