"""MotionModel — bind a physics3d engine to a WorldState Entity (tier B / P3).

The seam that turns the 2D scripted ``_advance`` into real 3D autonomous
maneuver. Each mobile entity gets an engine chosen by its catalog ``domain``:

    land  -> GroundVehicleEngine   drives waypoints over the shared Heightfield,
                                    hugging the surface (z/pitch/roll), climbing
                                    and slowing on grade (bicycle + pure-pursuit)
    air   -> FixedWing3DOFEngine    computes its own heading/altitude/speed to
                                    chase successive waypoints at a cruise altitude

The model owns the engine, advances it (the WorldState handles multi-rate
sub-stepping), and writes the result back onto the entity as a 3D
``position = [x, y, z]`` plus ``heading``. All distance/lethality math in feeds
and effects still reads only x/y, so carrying z is non-breaking; z is there for
the HUD and P4 (3D LOS / ballistics).

The engines live behind the same swappable boundary as the rest of physics3d, so
a JSBSim 6-DOF engine (P2) can replace the fixed-wing model here without changing
WorldState or any caller.
"""

from __future__ import annotations

import math
from typing import Optional

from ..physics3d import GroundVehicleEngine, Heightfield, make_air_engine

# Per-category ground speed defaults (m/s) when the catalog entry doesn't pin one
# in ``constraints.max_speed_mps``. Kept deliberate/plausible rather than maxed.
_GROUND_MAX_SPEED = {
    "armor": 8.0, "apc": 10.0, "infantry": 2.5,
    "artillery": 6.0, "air_defense": 7.0, "ew": 7.0,
}
_GROUND_DEFAULT_SPEED = 8.0

# Slow-UAV fixed-wing defaults (a TB2-class loiter platform, not a fighter) so the
# air model self-navigates at sane recon speeds. Overridable via catalog constraints.
_UAV_PARAMS = dict(
    mass=650.0, wing_area=12.0, cd0=0.03, k_induced=0.06, t_max=6000.0,
    v_stall=22.0, v_max=80.0, cruise=45.0, bank_max_deg=35.0, gamma_max_deg=12.0,
)
_UAV_AGL = 1500.0          # cruise height above the terrain at the start point
_AIR_CAPTURE = 200.0       # xy radius at which an air waypoint counts as reached


class MotionModel:
    """Wraps one physics engine and the entity it drives. ``step`` advances the
    engine and writes 3D state back onto the entity; ``retarget`` re-aims it (used
    by EffectResolver ISR reposition)."""

    def __init__(self, entity, engine, domain: str, *, route: Optional[list] = None,
                 cruise_alt: float = _UAV_AGL, capture: float = _AIR_CAPTURE,
                 orbit: Optional[dict] = None) -> None:
        self.entity = entity
        self.engine = engine
        self.domain = domain
        self.route = [list(p) for p in (route or [])]
        self.wi = 0
        self.cruise_alt = float(cruise_alt)
        self.capture = float(capture)
        self.orbit = orbit          # {"center": [x, y], "r": float, "alt": float} or None
        if domain == "air":
            self._aim_air()
        self._write(self.engine.state())   # entity gets correct 3D state on attach

    # ---- air waypoint sequencing ---------------------------------------- #
    def _aim_air(self) -> None:
        if self.wi < len(self.route):
            wp = self.route[self.wi]
            self.engine.set_target((wp[0], wp[1], self.cruise_alt))

    def _write(self, b) -> None:
        self.entity.position = [float(b.pos[0]), float(b.pos[1]), float(b.pos[2])]
        # heading from the engine's own steering state (robust at standstill)
        self.entity.heading = float(getattr(self.engine, "psi",
                                            getattr(self.engine, "chi", b.heading())))

    def _aim_orbit(self) -> None:
        """Chase a point led around the loiter circle, so a fixed-wing that can't
        hover flies a continuous orbit over the area of interest."""
        c = self.orbit
        cx, cy = float(c["center"][0]), float(c["center"][1])
        r = float(c.get("r", 800.0))
        alt = float(c.get("alt", self.cruise_alt))
        ang = math.atan2(self.engine.y - cy, self.engine.x - cx)
        lead = ang + 0.6                      # rad ahead → pulls the aircraft around
        self.engine.set_target((cx + r * math.cos(lead), cy + r * math.sin(lead), alt))

    # ---- step / queries ------------------------------------------------- #
    def step(self, dt: float):
        if self.domain == "air":
            if self.wi < len(self.route):
                wp = self.route[self.wi]
                if math.hypot(self.engine.x - wp[0], self.engine.y - wp[1]) <= self.capture:
                    self.wi += 1
                    self._aim_air()
            elif self.orbit is not None:
                self._aim_orbit()             # route done → loiter on station
        b = self.engine.step(dt)
        self._write(b)
        return b

    def arrived(self) -> bool:
        if self.domain == "air":
            return self.wi >= len(self.route)
        return self.engine.arrived()

    def orbiting(self) -> bool:
        return self.domain == "air" and self.orbit is not None and self.wi >= len(self.route)

    def set_route(self, wps, *, orbit: Optional[dict] = None,
                  cruise_alt: Optional[float] = None) -> None:
        """Re-task with a full multi-waypoint route (+ optional loiter orbit)."""
        self.route = [[float(p[0]), float(p[1])] for p in wps]
        self.wi = 0
        self.orbit = orbit
        if cruise_alt is not None:
            self.cruise_alt = float(cruise_alt)
        if self.domain == "air":
            self._aim_air()
        else:
            self.engine.set_waypoints(self.route)

    def retarget(self, xy) -> None:
        """Re-aim to a single point (ISR reposition). Resets waypoint sequencing."""
        pt = [float(xy[0]), float(xy[1])]
        self.route = [pt]
        self.wi = 0
        self.orbit = None
        if self.domain == "air":
            self._aim_air()
        else:
            self.engine.set_waypoints([pt])


# --------------------------------------------------------------------------- #
def attach_air_recon(entity, spec, terrain: Heightfield, route, *,
                     orbit: Optional[dict] = None, cruise_agl: float = _UAV_AGL) -> "MotionModel":
    """Attach an air MotionModel to a recon UAV that was spawned static (no motion
    at build time). Resets the engine at the UAV's CURRENT altitude so it climbs to
    cruise as it flies the route (an honest take-off/climb, not a teleport), then
    loiters on the orbit. Used when the commander/AIP first tasks an ISR sweep."""
    params = dict(_UAV_PARAMS)
    if spec is not None and spec.constraints.get("cruise_mps"):
        params["cruise"] = float(spec.constraints["cruise_mps"])
    x0, y0 = float(entity.position[0]), float(entity.position[1])
    z0 = float(entity.position[2]) if len(entity.position) > 2 else terrain.height(x0, y0)
    cruise_alt = terrain.height(x0, y0) + float(cruise_agl)
    eng = make_air_engine(getattr(entity, "hero", False), params, cruise_alt=cruise_alt)
    eng.reset((x0, y0, z0), V=params["cruise"])     # start where it sits → it climbs
    wps = [[float(p[0]), float(p[1])] for p in route]
    return MotionModel(entity, eng, "air", route=wps, cruise_alt=cruise_alt, orbit=orbit)


def build_motion(entity, spec, terrain: Heightfield) -> Optional[MotionModel]:
    """Construct the MotionModel for a mobile entity, dispatched on ``spec.domain``.

    Returns None for domains without a motion model (sea/space/cyber) — those fall
    back to the legacy scripted path. ``entity.position`` is the current 2D/3D
    spawn; ``entity.route`` the [[x,y],...] waypoints; ``entity._offset`` the
    per-unit formation offset (preserved so a group keeps its loose formation).
    """
    domain = getattr(spec, "domain", "land")
    route = entity.route or []
    ox, oy = entity._offset
    # waypoints carry the same formation offset the legacy _advance applied
    wps = [[float(p[0]) + ox, float(p[1]) + oy] for p in route]
    x0, y0 = float(entity.position[0]), float(entity.position[1])

    if domain == "land":
        max_speed = float(entity.speed_mps or
                          spec.constraints.get("max_speed_mps")
                          or _GROUND_MAX_SPEED.get(entity.category, _GROUND_DEFAULT_SPEED))
        eng = GroundVehicleEngine(terrain, max_speed=max_speed)
        # head toward the first waypoint beyond the spawn so it sets off correctly
        aim = next((w for w in wps if math.hypot(w[0] - x0, w[1] - y0) > 1.0), None)
        hdg = math.atan2(aim[1] - y0, aim[0] - x0) if aim else float(entity.heading)
        eng.reset((x0, y0), heading=hdg)
        eng.set_waypoints(wps)
        return MotionModel(entity, eng, "land", route=wps)

    if domain == "air":
        params = dict(_UAV_PARAMS)
        if spec.constraints.get("cruise_mps"):
            params["cruise"] = float(spec.constraints["cruise_mps"])
        alt = terrain.height(x0, y0) + _UAV_AGL
        # a hero aircraft flies a JSBSim 6-DOF FDM (P2); everyone else stays numpy.
        # make_air_engine falls back to the numpy model when jsbsim is unavailable.
        eng = make_air_engine(getattr(entity, "hero", False), params, cruise_alt=alt)
        eng.reset((x0, y0, alt), V=params["cruise"])
        return MotionModel(entity, eng, "air", route=wps, cruise_alt=alt)

    return None
