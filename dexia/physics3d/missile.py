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


class MissileEngine:
    def __init__(self, *, speed: float = 600.0, N: float = 4.0) -> None:
        self.speed = float(speed)
        self.N = float(N)
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
        self.vel = self.vel + a * dt
        sp = np.linalg.norm(self.vel)
        if sp > 1e-9:                       # solid motor holds speed; PN only turns
            self.vel = self.vel / sp * self.speed
        self.pos = self.pos + self.vel * dt
        return self.pos.copy()

    def intercept(self, p0, target_pos, target_vel=(0.0, 0.0, 0.0), *,
                  dt: float = 0.05, t_max: float = 30.0) -> float:
        """Fly to closest approach; return the miss distance (m). Convenience for
        a one-shot SAM resolution against a (constant-velocity) aircraft."""
        self.launch(p0, target_pos)
        tgt = np.asarray(target_pos, dtype=np.float64)
        tvel = np.asarray(target_vel, dtype=np.float64)
        miss, t = float(np.linalg.norm(tgt - self.pos)), 0.0
        while t < t_max:
            self.step(dt, tgt, tvel)
            tgt = tgt + tvel * dt
            t += dt
            d = float(np.linalg.norm(tgt - self.pos))
            if d > miss + 50.0:             # past closest approach
                break
            miss = min(miss, d)
        return miss

    def state(self) -> Body6:
        return Body6(pos=self.pos.copy(), vel=self.vel.copy())
