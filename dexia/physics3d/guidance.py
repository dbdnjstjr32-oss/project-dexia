"""Guidance laws — how an entity turns its goal into control inputs.

The "organic, goal-directed" layer. Global pathing (A*/Dubins) hands waypoints
to these local laws, which produce the steering/turn commands the motion models
integrate:

  pure_pursuit            wheeled/tracked vehicles -> steer angle to a lookahead pt
  seek / arrive           Reynolds steering -> desired velocity (smooth approach)
  proportional_navigation missiles/interceptors -> accel ⟂ LOS, nulls LOS rate
"""

from __future__ import annotations

import math

import numpy as np

from .integrate import wrap_angle


def pure_pursuit(pos2, heading: float, target2, wheelbase: float,
                 lookahead: float) -> float:
    """Bicycle-model steer angle [rad] to chase a lookahead point.
        delta = atan( 2 L sin(alpha) / L_d )
    alpha = bearing of the target relative to current heading."""
    dx = float(target2[0]) - float(pos2[0])
    dy = float(target2[1]) - float(pos2[1])
    alpha = wrap_angle(math.atan2(dy, dx) - heading)
    Ld = max(1e-3, lookahead)
    return math.atan2(2.0 * wheelbase * math.sin(alpha), Ld)


def seek(pos, target, v_max: float) -> np.ndarray:
    """Reynolds seek: desired velocity straight at the target at full speed."""
    d = np.asarray(target, float) - np.asarray(pos, float)
    n = np.linalg.norm(d)
    return (d / n) * v_max if n > 1e-9 else np.zeros_like(d)


def arrive(pos, target, v_max: float, slow_radius: float) -> np.ndarray:
    """Reynolds arrive: seek, but ramp speed down inside ``slow_radius`` so the
    entity decelerates to a stop on the target instead of overshooting."""
    d = np.asarray(target, float) - np.asarray(pos, float)
    dist = np.linalg.norm(d)
    if dist < 1e-6:
        return np.zeros_like(d)
    speed = v_max * min(1.0, dist / max(1e-6, slow_radius))
    return (d / dist) * speed


def proportional_navigation(r_rel, v_rel, v_missile, N: float = 4.0) -> np.ndarray:
    """True PN acceleration command (3-D vector form).

        Omega = (R x V_rel) / (R . R)        # LOS rotation rate vector
        a_cmd = N * (Omega x V_missile)      # ⟂ to missile velocity

    ``r_rel`` = target_pos - missile_pos, ``v_rel`` = target_vel - missile_vel.
    Driving the LOS rate to zero puts the missile on a collision triangle."""
    R = np.asarray(r_rel, float)
    V = np.asarray(v_rel, float)
    rr = float(np.dot(R, R))
    if rr < 1e-9:
        return np.zeros(3)
    omega = np.cross(R, V) / rr
    return N * np.cross(omega, np.asarray(v_missile, float))
