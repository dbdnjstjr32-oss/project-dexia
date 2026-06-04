"""Body6 — uniform 3D rigid-body state + quaternion utilities.

The common output state every motion model writes, so consumers (fusion, HUD)
see one shape regardless of which engine integrated it: world ENU position
[East, North, Up], world velocity, attitude quaternion [w,x,y,z], body rates.
Generalises ``DroneState6DOF`` (physics/base.py) beyond the drone case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


# --- quaternion helpers (q = [w, x, y, z], body->world) -------------------- #
def quat_from_euler(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """ZYX (yaw-pitch-roll) Euler angles [rad] -> unit quaternion."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return np.array([
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ], dtype=np.float64)


def quat_to_euler(q: np.ndarray) -> np.ndarray:
    """Unit quaternion -> [roll, pitch, yaw] (rad)."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    sp = 2 * (w * y - z * x)
    pitch = math.asin(max(-1.0, min(1.0, sp)))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return np.array([roll, pitch, yaw], dtype=np.float64)


def quat_normalize(q: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(q)
    return q / n if n > 1e-12 else np.array([1.0, 0.0, 0.0, 0.0])


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ], dtype=np.float64)


def quat_integrate(q: np.ndarray, omega: np.ndarray, dt: float) -> np.ndarray:
    """Integrate attitude by body rates: q_{k+1} = normalize(q + 0.5 (q⊗[0,ω]) dt)."""
    wq = np.array([0.0, omega[0], omega[1], omega[2]])
    return quat_normalize(q + 0.5 * quat_mul(q, wq) * dt)


# --- the state ------------------------------------------------------------- #
@dataclass
class Body6:
    pos: np.ndarray = field(default_factory=lambda: np.zeros(3))      # ENU [E, N, Up] m
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3))      # world m/s
    quat: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    omega: np.ndarray = field(default_factory=lambda: np.zeros(3))    # body rates rad/s

    def euler(self) -> np.ndarray:
        return quat_to_euler(self.quat)

    def speed(self) -> float:
        return float(np.linalg.norm(self.vel))

    def heading(self) -> float:
        return math.atan2(self.vel[1], self.vel[0])

    def alt(self) -> float:
        return float(self.pos[2])

    def copy(self) -> "Body6":
        return Body6(self.pos.copy(), self.vel.copy(), self.quat.copy(), self.omega.copy())

    def to_dict(self) -> dict:
        r, p, yaw = self.euler()
        return {
            "pos": [round(float(v), 2) for v in self.pos],
            "vel": [round(float(v), 2) for v in self.vel],
            "euler_deg": [round(math.degrees(a), 1) for a in (r, p, yaw)],
            "speed": round(self.speed(), 2), "alt": round(self.alt(), 1),
        }
