"""GroundVehicleEngine — kinematic bicycle model on terrain.

A wheeled/tracked vehicle that drives itself to waypoints over a Heightfield:

  * heading from the **kinematic bicycle model**  psi_dot = (v/L) tan(delta)
  * steering from **pure pursuit** to a lookahead point on the route
  * speed governed by drivetrain accel, **cornering limit** v^2/R <= mu g, and
    the **along-slope gravity** component (slows uphill, gains downhill)
  * z, pitch, roll read off the terrain surface so it hugs the ground in 3-D
"""

from __future__ import annotations

import math

import numpy as np

from .guidance import pure_pursuit
from .integrate import clamp
from .state import Body6, quat_from_euler
from .terrain import Heightfield

G = 9.80665


class GroundVehicleEngine:
    def __init__(self, terrain: Heightfield | None = None, *, wheelbase: float = 3.0,
                 max_speed: float = 15.0, max_accel: float = 3.0, max_steer: float = 0.6,
                 mu: float = 0.7, lookahead: float = 10.0, capture: float = 8.0,
                 speed_gain: float = 1.0) -> None:
        self.terrain = terrain or Heightfield.flat()
        self.L = wheelbase
        self.max_speed = max_speed
        self.max_accel = max_accel
        self.max_steer = max_steer
        self.mu = mu
        self.lookahead = lookahead
        self.capture = capture
        self.kv = speed_gain
        self.x = self.y = self.psi = self.v = 0.0
        self.wps: list = []
        self.wi = 0

    def reset(self, pos2, heading: float = 0.0, speed: float = 0.0) -> Body6:
        self.x, self.y = float(pos2[0]), float(pos2[1])
        self.psi, self.v = float(heading), float(speed)
        return self.state()

    def set_waypoints(self, wps) -> None:
        self.wps = [np.asarray(w[:2], float) for w in wps]
        self.wi = 0

    def arrived(self) -> bool:
        return self.wi >= len(self.wps)

    # ------------------------------------------------------------------ #
    def step(self, dt: float) -> Body6:
        # advance the waypoint index when close enough
        while self.wi < len(self.wps) and \
                math.hypot(self.wps[self.wi][0] - self.x, self.wps[self.wi][1] - self.y) <= self.capture:
            self.wi += 1

        gx, gy = self.terrain.grad(self.x, self.y)
        along = gx * math.cos(self.psi) + gy * math.sin(self.psi)   # slope along heading
        cross = -gx * math.sin(self.psi) + gy * math.cos(self.psi)  # slope across heading
        a_grav = -G * math.sin(math.atan(along))                    # uphill -> decel

        if self.arrived():
            steer = 0.0
            v_cmd = 0.0
        else:
            tgt = self.wps[self.wi]
            steer = clamp(pure_pursuit((self.x, self.y), self.psi, tgt, self.L, self.lookahead),
                          -self.max_steer, self.max_steer)
            # cornering speed limit: v^2/R <= mu g, with R = L / tan(delta)
            if abs(steer) > 1e-3:
                R = self.L / math.tan(abs(steer))
                v_corner = math.sqrt(self.mu * G * R)
            else:
                v_corner = self.max_speed
            v_cmd = min(self.max_speed, v_corner)

        a_drive = clamp(self.kv * (v_cmd - self.v), -self.max_accel, self.max_accel)
        self.v = max(0.0, self.v + (a_drive + a_grav) * dt)
        self.psi += (self.v / self.L) * math.tan(steer) * dt
        self.x += self.v * math.cos(self.psi) * dt
        self.y += self.v * math.sin(self.psi) * dt
        return self.state(along)

    # ------------------------------------------------------------------ #
    def state(self, along: float | None = None) -> Body6:
        z = self.terrain.height(self.x, self.y)
        gx, gy = self.terrain.grad(self.x, self.y)
        if along is None:
            along = gx * math.cos(self.psi) + gy * math.sin(self.psi)
        cross = -gx * math.sin(self.psi) + gy * math.cos(self.psi)
        pitch = math.atan(along)       # climbing (along>0) -> nose up (pitch>0)
        roll = math.atan(cross)
        vz = self.v * along            # vertical rate from climbing the surface
        return Body6(
            pos=np.array([self.x, self.y, z]),
            vel=np.array([self.v * math.cos(self.psi), self.v * math.sin(self.psi), vz]),
            quat=quat_from_euler(roll, pitch, self.psi),
        )
