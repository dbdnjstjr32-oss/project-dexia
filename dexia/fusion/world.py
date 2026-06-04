"""WorldState — ground truth (AIP fusion substrate).

Expands a validated Scenario + Catalog into concrete entities and steps their
scripted motion. This is the *truth* the feeds observe imperfectly; it needs no
physics engine (DESIGN_AIP.md decision: scripted ground truth is enough to prove
fusion + asset selection). Blue assets are known; red entities are only ever
*seen* through feeds, becoming fused Tracks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..physics3d import Heightfield
from ..scenario.catalog import Catalog, get_catalog
from ..scenario.scenario import Scenario
from .motion import build_motion

ADVANCE_SPEED_MPS = 4.0       # ~14 km/h deliberate advance under threat
DEFAULT_PHYSICS_HZ = 20       # physics sub-steps per second (>> the 1/12-tick decision rate)
JAM_PERIOD_TICKS = 30         # periodic_jam emitter blink half-period
DEFAULT_ROUNDS = 10           # ammo for a platform that has an ammo limit but no count


@dataclass
class Entity:
    """One concrete unit on the field (ground truth)."""

    entity_id: str
    cls: str                              # catalog key
    side: str                             # blue | red
    category: str = ""
    position: list = field(default_factory=lambda: [0.0, 0.0])
    heading: float = 0.0
    emitting: bool = False                # radar/jammer radiating this tick
    alive: bool = True
    behavior: str = "static"
    route: Optional[list] = None          # [[x,y], ...] waypoints
    ammo: float = float("inf")            # rounds remaining (inf = not ammo-limited)
    suppressed_until: int = -1            # tick through which EW jamming forces emitter off
    speed_mps: float = 0.0                # override advance speed (0 => ADVANCE_SPEED_MPS)
    _leg: int = 1                         # index of the current target waypoint
    _offset: tuple = (0.0, 0.0)           # per-unit formation offset
    _motion: object = None                # MotionModel (3D physics path) or None (scripted)

    def xy(self) -> tuple:
        return float(self.position[0]), float(self.position[1])


def _build_terrain(spec: dict) -> Heightfield:
    """Turn a scenario ``terrain:`` block into a Heightfield via the existing
    constructors. Tolerant — unknown/missing keys fall back to sensible defaults."""
    kind = str(spec.get("type", "flat")).lower()
    if kind == "hill":
        return Heightfield.hill(
            peak=float(spec.get("peak", 300.0)),
            center=tuple(spec.get("center", (2000.0, 0.0))),
            sigma=float(spec.get("sigma", 800.0)),
            size=int(spec.get("size", 81)),
            dx=float(spec.get("dx", 50.0)),
            origin=tuple(spec.get("origin", (-2000.0, -2000.0))),
        )
    if kind == "ramp":
        return Heightfield.ramp(
            slope=float(spec.get("slope", 0.05)),
            size=int(spec.get("size", 64)),
            dx=float(spec.get("dx", 50.0)),
            origin=tuple(spec.get("origin", (0.0, 0.0))),
        )
    return Heightfield.flat(float(spec.get("z0", 0.0)))


class WorldState:
    def __init__(self, entities: list, catalog: Optional[Catalog] = None, *,
                 terrain: Optional[Heightfield] = None,
                 physics_hz: int = DEFAULT_PHYSICS_HZ) -> None:
        self.entities: list = entities
        self.catalog = catalog or get_catalog()
        self.terrain = terrain
        self.physics = terrain is not None      # 3D motion gated on terrain presence
        self.physics_hz = physics_hz
        self.tick = 0
        self.t = 0.0

    # ---- views ----------------------------------------------------------- #
    @property
    def blue(self) -> list:
        return [e for e in self.entities if e.side == "blue" and e.alive]

    @property
    def red(self) -> list:
        return [e for e in self.entities if e.side == "red" and e.alive]

    def get(self, entity_id: str) -> Optional[Entity]:
        return next((e for e in self.entities if e.entity_id == entity_id), None)

    # ---- construction ---------------------------------------------------- #
    @classmethod
    def from_scenario(cls, scenario: Scenario, catalog: Optional[Catalog] = None) -> "WorldState":
        catalog = catalog or get_catalog()
        terrain = _build_terrain(scenario.terrain) if scenario.terrain else None
        entities: list = []
        for side, forces in (("blue", scenario.blue), ("red", scenario.red)):
            for fe in forces:
                spec = catalog.get(fe.cls)
                category = (spec.category if spec and spec.category
                            else (spec.role if spec else ""))
                anchor = (fe.route[0] if fe.route else fe.pos) or [0.0, 0.0]
                if spec and spec.has_ammo_limit:
                    ammo = (1 if spec.constraints.get("one_shot")
                            else spec.constraints.get("rounds", DEFAULT_ROUNDS))
                else:
                    ammo = float("inf")
                for i in range(max(1, fe.n)):
                    # spread a group perpendicular to keep a loose formation
                    off = (0.0, (i - (fe.n - 1) / 2.0) * 60.0)
                    pos = [float(anchor[0]) + off[0], float(anchor[1]) + off[1]]
                    if terrain is not None:
                        pos.append(terrain.height(pos[0], pos[1]))   # 3D spawn on the surface
                    e = Entity(
                        entity_id=f"{fe.cls}_{i}" if fe.n > 1 else fe.cls,
                        cls=fe.cls, side=side, category=category,
                        position=pos, behavior=fe.behavior,
                        route=[list(p) for p in fe.route] if fe.route else None,
                        emitting=_initial_emitting(fe.behavior, spec),
                        ammo=ammo, _offset=off,
                    )
                    # gate the 3D physics path on terrain presence: mobile entities
                    # get a per-domain motion model; everyone else stays scripted.
                    if terrain is not None and spec is not None \
                            and fe.behavior == "advance" and e.route:
                        e._motion = build_motion(e, spec, terrain)
                    entities.append(e)
        return cls(entities, catalog, terrain=terrain)

    # ---- scripted dynamics ---------------------------------------------- #
    def step(self, dt: float = 1.0) -> "WorldState":
        self.tick += 1
        self.t += dt
        for e in self.entities:
            if not e.alive:
                continue
            if e._motion is not None:
                # multi-rate: physics sub-steps within this decision/perception tick
                n = max(1, int(round(self.physics_hz * dt)))
                sub = dt / n
                for _ in range(n):
                    e._motion.step(sub)
            elif e.behavior == "advance" and e.route:
                self._advance(e, dt)
            if e.behavior == "periodic_jam":
                e.emitting = (self.tick // JAM_PERIOD_TICKS) % 2 == 0
            # EW suppression overrides any emission while the jam window is open
            if self.tick <= e.suppressed_until:
                e.emitting = False
        return self

    def _advance(self, e: Entity, dt: float) -> None:
        if e._leg >= len(e.route):
            return
        tgt = [e.route[e._leg][0] + e._offset[0], e.route[e._leg][1] + e._offset[1]]
        dx, dy = tgt[0] - e.position[0], tgt[1] - e.position[1]
        dist = math.hypot(dx, dy)
        step = (e.speed_mps or ADVANCE_SPEED_MPS) * dt
        if dist <= step:
            e.position = list(tgt)
            e._leg += 1
        else:
            e.position = [e.position[0] + dx / dist * step,
                          e.position[1] + dy / dist * step]
            e.heading = math.atan2(dy, dx)


def _initial_emitting(behavior: str, spec) -> bool:
    """Air-defense radars (static_ad) radiate continuously; jammers blink."""
    if spec is None or not spec.emits:
        return False
    return behavior in ("static_ad",) or behavior == "periodic_jam"
