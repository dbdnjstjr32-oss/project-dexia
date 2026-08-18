"""3D geometry helpers shared by fusion feeds + effects (tier B / P4).

Deliberately z-safe: a 2-element [x,y] position (the legacy / no-terrain world)
is treated as z=0, so ``distance3d`` collapses to the old 2D ``hypot`` and every
P4 path is a no-op without terrain — the 100-scenario campaign is unchanged.
"""

from __future__ import annotations

import math
from typing import Optional


def _z(p) -> float:
    return float(p[2]) if len(p) > 2 else 0.0


def distance3d(a, b) -> float:
    """Euclidean distance in 3D; absent z is 0 (flat ground -> equals 2D distance)."""
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    dz = _z(a) - _z(b)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def clear_los(terrain, p0, p1) -> bool:
    """True if p0 can see p1. No terrain (None / flat) never occludes -> always
    True, so detection is identical to pre-P4 when a world has no heightfield."""
    if terrain is None:
        return True
    a = (float(p0[0]), float(p0[1]), _z(p0))
    b = (float(p1[0]), float(p1[1]), _z(p1))
    return terrain.raycast(a, b) is None
