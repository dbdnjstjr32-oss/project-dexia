"""BallisticEngine — point-mass projectile with drag (tier B / P4).

An artillery/tank round as a 6-state point mass [x,y,z, vx,vy,vz] under gravity
and quadratic drag, integrated with the shared RK4. The launch solution is the
vacuum lob that reaches the aimpoint in the catalog time-of-flight; drag then
bends the *flown* arc realistically. The kill is still decided by the aimpoint +
``lethal_r`` in the EffectResolver, so flat-ground outcomes are unchanged — this
engine gives the shell a real 3D trajectory (for the HUD) and an honest apex.
"""

from __future__ import annotations

import numpy as np

from .integrate import rk4

G = 9.80665


class BallisticEngine:
    def __init__(self, *, drag_k: float = 1.5e-4) -> None:
        # drag as an acceleration coefficient: a_drag = -k |v| v  (k = ½ρ Cd A / m)
        self.k = float(drag_k)
        self.y = np.zeros(6, dtype=np.float64)

    def solve(self, p0, target, tof: float) -> "BallisticEngine":
        """Set the launch state for a lob from p0 to target in ``tof`` seconds."""
        p0 = np.asarray(p0, dtype=np.float64)
        tg = np.asarray(target, dtype=np.float64)
        if p0.size < 3:
            p0 = np.array([p0[0], p0[1], 0.0])
        if tg.size < 3:
            tg = np.array([tg[0], tg[1], 0.0])
        tof = max(1e-3, float(tof))
        d = tg - p0
        v = np.array([d[0] / tof, d[1] / tof, (d[2] + 0.5 * G * tof * tof) / tof])
        self.y = np.concatenate([p0, v])
        return self

    def _f(self, y: np.ndarray) -> np.ndarray:
        v = y[3:]
        a = np.array([0.0, 0.0, -G]) - self.k * float(np.linalg.norm(v)) * v
        return np.concatenate([v, a])

    def step(self, dt: float) -> np.ndarray:
        self.y = rk4(self._f, self.y, dt)
        return self.y[:3].copy()

    @property
    def pos(self) -> np.ndarray:
        return self.y[:3].copy()

    @property
    def vel(self) -> np.ndarray:
        return self.y[3:].copy()

    def trajectory(self, p0, target, tof: float, *, dt: float = 0.25,
                   ground_z: float | None = None) -> list:
        """The sampled 3D arc from launch until the round falls back to the
        aimpoint altitude (or ``ground_z``). Returns a list of [x,y,z] points."""
        self.solve(p0, target, tof)
        tg = np.asarray(target, dtype=np.float64)
        ground = float(ground_z) if ground_z is not None else (float(tg[2]) if tg.size > 2 else 0.0)
        pts = [self.pos]
        t, t_max = 0.0, tof * 3.0 + dt
        while t < t_max:
            self.step(dt)
            t += dt
            pts.append(self.pos)
            if self.vel[2] < 0.0 and self.pos[2] <= ground:
                break
        return [[round(float(c), 2) for c in p] for p in pts]

    @staticmethod
    def apex(traj: list) -> float:
        """Peak altitude of a trajectory (for the HUD / verification)."""
        return max(p[2] for p in traj) if traj else 0.0
