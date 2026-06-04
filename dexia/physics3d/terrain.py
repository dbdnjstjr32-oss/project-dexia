"""Heightfield — the shared 3D terrain substrate.

A digital elevation grid z = H(x, y). Ground vehicles sit on it (z, pitch, roll
from the surface), aircraft check clearance against it, and line-of-sight can be
occluded by it. Bilinear interpolation for height; central differences for the
surface gradient -> normal/slope. Backed by a NumPy array (procedural for now,
swappable for a real DEM via rasterio later).
"""

from __future__ import annotations

import math

import numpy as np


class Heightfield:
    def __init__(self, heights=None, dx: float = 10.0,
                 origin=(0.0, 0.0), z0: float = 0.0) -> None:
        self.dx = float(dx)
        self.ox, self.oy = float(origin[0]), float(origin[1])
        self.z0 = float(z0)
        self.H = None if heights is None else np.asarray(heights, dtype=np.float64)

    # ---- queries --------------------------------------------------------- #
    def height(self, x: float, y: float) -> float:
        if self.H is None:
            return self.z0
        gx = (x - self.ox) / self.dx
        gy = (y - self.oy) / self.dx
        ny, nx = self.H.shape
        i0 = int(math.floor(gx)); j0 = int(math.floor(gy))
        i0 = max(0, min(nx - 2, i0)); j0 = max(0, min(ny - 2, j0))
        fx = max(0.0, min(1.0, gx - i0)); fy = max(0.0, min(1.0, gy - j0))
        h00 = self.H[j0, i0]; h10 = self.H[j0, i0 + 1]
        h01 = self.H[j0 + 1, i0]; h11 = self.H[j0 + 1, i0 + 1]
        return float((h00 * (1 - fx) + h10 * fx) * (1 - fy)
                     + (h01 * (1 - fx) + h11 * fx) * fy)

    def grad(self, x: float, y: float) -> tuple:
        """(dz/dx, dz/dy) by central difference [m/m]."""
        if self.H is None:
            return 0.0, 0.0
        d = self.dx
        gx = (self.height(x + d, y) - self.height(x - d, y)) / (2 * d)
        gy = (self.height(x, y + d) - self.height(x, y - d)) / (2 * d)
        return gx, gy

    def normal(self, x: float, y: float) -> np.ndarray:
        gx, gy = self.grad(x, y)
        n = np.array([-gx, -gy, 1.0])
        return n / np.linalg.norm(n)

    def raycast(self, p0, p1, max_step: float | None = None):
        """March the 3D segment p0->p1 (ENU [x,y,z]) over the surface and return the
        first point where terrain rises above the ray (the blocker), or ``None`` if
        the line of sight is clear. ``None`` is also returned for an unset (flat)
        heightfield — flat ground never occludes. Endpoints are excluded so a
        sensor/target sitting *on* the surface isn't self-blocked."""
        if self.H is None:
            return None
        a = np.asarray(p0, dtype=np.float64)
        b = np.asarray(p1, dtype=np.float64)
        horiz = math.hypot(b[0] - a[0], b[1] - a[1])
        if horiz < 1e-9:
            return None
        step = max_step or self.dx
        n = max(2, int(math.ceil(horiz / step)))
        for i in range(1, n):                       # skip both endpoints
            t = i / n
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            z = a[2] + (b[2] - a[2]) * t
            if self.height(x, y) > z + 1e-6:        # terrain pokes above the sightline
                return np.array([x, y, z])
        return None

    def slope(self, x: float, y: float) -> float:
        """Steepest-descent slope angle [rad]."""
        gx, gy = self.grad(x, y)
        return math.atan(math.hypot(gx, gy))

    # ---- constructors ---------------------------------------------------- #
    @classmethod
    def flat(cls, z0: float = 0.0) -> "Heightfield":
        return cls(None, z0=z0)

    @classmethod
    def ramp(cls, slope: float = 0.05, *, size: int = 64, dx: float = 50.0,
             origin=(0.0, 0.0)) -> "Heightfield":
        """A planar slope rising in +x by ``slope`` (rise/run)."""
        xs = (np.arange(size) * dx)
        H = np.tile(xs * slope, (size, 1))            # rises with x (columns)
        return cls(H, dx=dx, origin=origin)

    @classmethod
    def hill(cls, peak: float = 300.0, center=(2000.0, 0.0), sigma: float = 800.0,
             *, size: int = 81, dx: float = 50.0, origin=(-2000.0, -2000.0)) -> "Heightfield":
        """A single Gaussian hill — good for terrain-follow / occlusion tests."""
        ys = origin[1] + np.arange(size) * dx
        xs = origin[0] + np.arange(size) * dx
        X, Y = np.meshgrid(xs, ys)
        H = peak * np.exp(-(((X - center[0]) ** 2 + (Y - center[1]) ** 2) / (2 * sigma ** 2)))
        return cls(H, dx=dx, origin=origin)
