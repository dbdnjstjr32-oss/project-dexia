"""Ground threat: Anti-Air (AA) battery (Phase 4 wargame element).

A static AA battery with:
  * a **radar detection cone** (apex at the battery, an axis direction, a
    half-angle and a max range) — drones inside the cone are "tracked";
  * a **fire cycle** — when it has a track and is off cooldown, it engages the
    nearest tracked drone by emitting a lethal **threat zone** (a sphere) at the
    target's position;
  * **lethality** — any drone intersecting an active threat zone is instantly
    destroyed (this feeds the environment's ``Total_Loss`` term).

The battery is a self-contained component (pure NumPy, no engine/env coupling),
so it can be dropped into any environment that can supply per-drone positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ThreatZone:
    """A lethal volume emitted by the battery (e.g. flak / proximity burst)."""

    center: np.ndarray
    radius: float
    ttl: float        # remaining lifetime in **seconds** (Hz-independent)
    origin_step: int

    @staticmethod
    def _segment_sphere_intersects(
        prev: np.ndarray, curr: np.ndarray,
        center: np.ndarray, radius: float,
    ) -> bool:
        """True if the line segment prev->curr passes within *radius* of *center*.

        Parametric form: P(t) = prev + t*(curr-prev), t in [0,1].
        Minimise |P(t) - center|^2 over t in [0,1] and check against radius^2.
        """
        d = curr - prev
        f = prev - center
        a = float(np.dot(d, d))
        if a < 1e-12:   # segment is a point
            return bool(np.dot(f, f) <= radius * radius)
        b = 2.0 * float(np.dot(f, d))
        c = float(np.dot(f, f)) - radius * radius
        discriminant = b * b - 4.0 * a * c
        if discriminant < 0.0:
            return False
        sqrt_disc = np.sqrt(discriminant)
        t1 = (-b - sqrt_disc) / (2.0 * a)
        t2 = (-b + sqrt_disc) / (2.0 * a)
        # Intersection exists if any part of [t1, t2] overlaps [0, 1]
        return t1 <= 1.0 and t2 >= 0.0

    def intersects_trajectory(
        self, prev: np.ndarray, curr: np.ndarray
    ) -> bool:
        """True if the step trajectory prev->curr passes through this zone."""
        return self._segment_sphere_intersects(
            np.asarray(prev, dtype=np.float64).reshape(3),
            np.asarray(curr, dtype=np.float64).reshape(3),
            self.center,
            self.radius,
        )

    # Legacy point-in-sphere helper kept for non-trajectory callers.
    def contains(self, pos: np.ndarray) -> bool:
        return bool(np.linalg.norm(pos - self.center) <= self.radius)


class AntiAirBattery:
    def __init__(
        self,
        position=(0.0, 0.0, 0.0),
        radar_dir=(0.0, 0.0, 1.0),
        radar_range: float = 8.0,
        radar_half_angle_deg: float = 75.0,
        fire_cooldown: int = 6,
        kill_radius: float = 1.5,
        zone_ttl: float = 4.0,
        dt: float = 0.02,
        ammo: int = 24,
        ew_range: float | None = None,
        seed: int | None = None,
    ) -> None:
        self.position = np.asarray(position, dtype=np.float64).reshape(3)
        d = np.asarray(radar_dir, dtype=np.float64).reshape(3)
        n = np.linalg.norm(d)
        self.radar_dir = d / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
        self.radar_range = float(radar_range)
        self.radar_half_angle = np.deg2rad(float(radar_half_angle_deg))
        self._cos_half = float(np.cos(self.radar_half_angle))
        self.fire_cooldown = int(fire_cooldown)
        self.kill_radius = float(kill_radius)          # lethal engagement radius
        self.zone_ttl = float(zone_ttl)               # zone lifetime in seconds
        self.dt = float(dt)                            # simulation step size [s]
        # EW / jamming radius (comms disruption) — distinct, wider than radar.
        self.ew_range = float(ew_range) if ew_range is not None else float(radar_range) * 1.4
        self.max_ammo = int(ammo)
        self.ammo = int(ammo)
        self._rng = np.random.default_rng(seed)

        self.zones: list[ThreatZone] = []
        self._cooldown = 0
        self._step = 0

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self.ammo = self.max_ammo
        self.zones = []
        self._cooldown = 0
        self._step = 0

    def in_radar(self, pos: np.ndarray) -> bool:
        """True if ``pos`` is inside the radar range AND detection cone."""
        d = np.asarray(pos, dtype=np.float64).reshape(3) - self.position
        r = float(np.linalg.norm(d))
        if r > self.radar_range:
            return False
        if r < 1e-9:
            return True
        cos_ang = float(np.dot(d / r, self.radar_dir))
        return cos_ang >= self._cos_half

    # ------------------------------------------------------------------ #
    def update(
        self,
        positions: dict[str, np.ndarray],
        prev_positions: dict[str, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """Advance the battery one step against the given live drone positions.

        Parameters
        ----------
        positions : mapping agent_id -> world position (only LIVE drones).
        prev_positions : mapping agent_id -> position at the PREVIOUS step.
            When provided, the lethality test uses a segment-sphere intersection
            (prev_pos -> curr_pos vs each zone sphere) so fast-moving drones
            cannot tunnel through a lethal zone in a single step.
            Falls back to a point test when prev_positions is None or missing
            for a given agent.

        Returns
        -------
        dict with keys: ``tracked`` (list of ids in the cone), ``fired`` (bool),
        ``engaged`` (id targeted this step or None), ``destroyed`` (set of ids
        intersecting an active threat zone), ``zones`` (list of active zones).
        """
        self._step += 1
        dt = self.dt

        # 1) age existing zones by dt seconds (Hz-independent)
        for z in self.zones:
            z.ttl -= dt
        self.zones = [z for z in self.zones if z.ttl > 0.0]

        # 2) radar scan
        tracked = [aid for aid, p in positions.items() if self.in_radar(p)]

        # 3) fire control (consumes ammo)
        fired = False
        engaged = None
        if tracked and self._cooldown <= 0 and self.ammo > 0:
            # engage the nearest tracked drone
            engaged = min(tracked, key=lambda a: np.linalg.norm(positions[a] - self.position))
            self.zones.append(
                ThreatZone(
                    center=np.asarray(positions[engaged], dtype=np.float64).copy(),
                    radius=self.kill_radius,
                    ttl=self.zone_ttl,   # stored as seconds
                    origin_step=self._step,
                )
            )
            self._cooldown = self.fire_cooldown
            self.ammo = max(0, self.ammo - 1)
            fired = True
        else:
            self._cooldown = max(0, self._cooldown - 1)

        # 4) lethality: segment-sphere intersection (anti-tunneling)
        destroyed = set()
        for aid, curr_p in positions.items():
            curr = np.asarray(curr_p, dtype=np.float64).reshape(3)
            # Use previous position for segment test when available.
            if prev_positions is not None and aid in prev_positions:
                prev = np.asarray(prev_positions[aid], dtype=np.float64).reshape(3)
            else:
                prev = curr   # degrade gracefully to point test
            for z in self.zones:
                if z.intersects_trajectory(prev, curr):
                    destroyed.add(aid)
                    break

        return {
            "tracked": tracked,
            "fired": fired,
            "engaged": engaged,
            "destroyed": destroyed,
            "zones": list(self.zones),
            "cooldown": self._cooldown,
        }

    # ------------------------------------------------------------------ #
    def telemetry(self) -> dict[str, Any]:
        return {
            "position": self.position.copy(),
            "radar_range": self.radar_range,
            "engagement_range": self.kill_radius,   # lethal inner radius
            "ew_range": self.ew_range,              # comms-disruption radius
            "radar_half_angle_deg": float(np.rad2deg(self.radar_half_angle)),
            "radar_dir": self.radar_dir.copy(),
            "ammo": int(self.ammo),
            "max_ammo": int(self.max_ammo),
            "active_zones": [(z.center.copy(), z.radius, z.ttl) for z in self.zones],
        }
