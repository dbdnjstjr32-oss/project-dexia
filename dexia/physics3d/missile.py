"""MissileEngine — proportional-navigation interceptor (tier B / P4).

A point-mass missile whose motor holds a constant speed and whose lateral
acceleration comes from the true PN law (``guidance.proportional_navigation``,
already proven in test_physics3d.py). Used to resolve a SAM engagement of an
aircraft: launched at the target, it nulls the line-of-sight rate onto a
collision course. Writes the uniform ``Body6`` like every other engine.
"""

from __future__ import annotations

import numpy as np

from .guidance import proportional_navigation
from .state import Body6


def _seg_min_distance(m0, m1, t0, t1) -> float:
    """Closest distance between two points moving linearly over the same step:
    the missile from ``m0``->``m1`` and the target from ``t0``->``t1``. Minimises
    |rel(s)| for s in [0,1] analytically, so a missile that passes within the
    warhead radius *between* samples is detected (a coarse dt no longer turns a
    real hit into a sampled miss — C-4)."""
    r0 = m0 - t0
    dr = (m1 - m0) - (t1 - t0)
    a = float(np.dot(dr, dr))
    if a < 1e-12:
        return float(np.linalg.norm(r0))
    s = -float(np.dot(r0, dr)) / a
    s = max(0.0, min(1.0, s))
    return float(np.linalg.norm(r0 + s * dr))


class MissileEngine:
    def __init__(self, *, speed: float = 600.0, N: float = 4.0,
                 max_lateral_g: float = 50.0) -> None:
        self.speed = float(speed)
        self.N = float(N)
        # Physical airframe limit on PN lateral acceleration. The raw PN command
        # is unbounded, which let the point mass execute instantaneous turns
        # (non-physical — C-3); a finite load factor makes a fast crosser realistically
        # missable instead of always reachable.
        self.max_lat_acc = float(max_lateral_g) * 9.80665
        self.pos = np.zeros(3, dtype=np.float64)
        self.vel = np.zeros(3, dtype=np.float64)

    def launch(self, p0, target_pos) -> "MissileEngine":
        """Boost off the rail pointed straight at the current target."""
        self.pos = np.asarray(p0, dtype=np.float64).copy()
        d = np.asarray(target_pos, dtype=np.float64) - self.pos
        n = np.linalg.norm(d)
        self.vel = (d / n) * self.speed if n > 1e-9 else np.array([self.speed, 0.0, 0.0])
        return self

    def step(self, dt: float, target_pos, target_vel=(0.0, 0.0, 0.0)) -> np.ndarray:
        r_rel = np.asarray(target_pos, dtype=np.float64) - self.pos
        v_rel = np.asarray(target_vel, dtype=np.float64) - self.vel
        a = proportional_navigation(r_rel, v_rel, self.vel, self.N)
        amag = float(np.linalg.norm(a))
        if amag > self.max_lat_acc:         # cap to a physical load factor (C-3)
            a = a / amag * self.max_lat_acc
        self.vel = self.vel + a * dt
        sp = np.linalg.norm(self.vel)
        if sp > 1e-9:                       # solid motor holds speed; PN only turns
            self.vel = self.vel / sp * self.speed
        self.pos = self.pos + self.vel * dt
        return self.pos.copy()

    def intercept(self, p0, target_pos, target_vel=(0.0, 0.0, 0.0), *,
                  dt: float = 0.02, t_max: float = 30.0) -> float:
        """Fly to closest approach; return the miss distance (m). Convenience for
        a one-shot SAM resolution against a (constant-velocity) aircraft.

        The miss is the minimum distance over each step *segment* (not just the
        sampled endpoints), so the warhead radius is compared against the true
        closest approach regardless of dt (C-4)."""
        self.launch(p0, target_pos)
        tgt = np.asarray(target_pos, dtype=np.float64).copy()
        tvel = np.asarray(target_vel, dtype=np.float64)
        miss, t = float(np.linalg.norm(tgt - self.pos)), 0.0
        while t < t_max:
            m_prev = self.pos.copy()
            t_prev = tgt.copy()
            self.step(dt, tgt, tvel)
            tgt = tgt + tvel * dt
            t += dt
            miss = min(miss, _seg_min_distance(m_prev, self.pos, t_prev, tgt))
            d = float(np.linalg.norm(tgt - self.pos))
            if d > miss + 200.0:            # comfortably past closest approach
                break
        return miss

    def state(self) -> Body6:
        return Body6(pos=self.pos.copy(), vel=self.vel.copy())
