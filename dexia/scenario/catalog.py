"""Equipment Catalog — the data-driven capability ontology (AIP data spine).

Every platform (blue and red) is one declarative entry in
``equipment_catalog.yaml``. A single entry drives FIVE subsystems, so adding a
new weapon/sensor is a data edit, not code:

    sensors[]   -> feed observation models   (what it sees, at what precision)
    emits       -> fusion detectability       (can SIGINT pick it up)
    effects[]   -> AssetMatch feasibility + sim effect resolution
    commands[]  -> ActionTypes it accepts     (auto-surfaced to the LLM as tools)
    constraints -> ammo / cooldown / readiness

Pure dataclasses + PyYAML; interpreter-agnostic and never touches the physics
hot loop. This is the OMS role for *equipment types* (cf. ontology/schema.py for
*instance* objects). See DESIGN_AIP.md section 2.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CATALOG_PATH = os.path.join(_ROOT, "equipment_catalog.yaml")

# Controlled vocabularies (kept permissive — the loader never rejects unknowns,
# it just exposes them; validation against scenarios happens in scenario.py).
SIDES = ("blue", "red")
DOMAINS = ("land", "air", "sea", "space", "cyber")
ROLES = ("fires", "isr", "strike", "ew", "air_defense", "maneuver", "sensor")
CATEGORIES = ("armor", "apc", "infantry", "artillery", "air_defense", "ew",
              "emitter", "air", "sea", "structure")


# --------------------------------------------------------------------------- #
# Which sensor types are blocked by terrain line-of-sight (P4). Optical/IR needs a
# clear sightline; radar (diffraction) and the passive RF/acoustic feeds see past
# a ridge. Overridable per entry via ``terrain_occludes``.
_TERRAIN_OCCLUDES = {"eo_ir": True, "radar": False, "sigint": False, "acoustic": False}


@dataclass
class SensorSpec:
    """A sensor an equipment provides — the origin of a feed's observations."""

    type: str = "eo_ir"                  # eo_ir | radar | sigint | acoustic
    range_m: float = 0.0
    fov_deg: float = 360.0
    pos_noise_m: float = 0.0             # 1-sigma position error it reports with
    detects: list = field(default_factory=list)   # categories it can see
    terrain_occludes: bool = True        # blocked by terrain LOS? (P4; default per type)

    @classmethod
    def from_dict(cls, d: dict) -> "SensorSpec":
        d = d or {}
        stype = str(d.get("type", "eo_ir"))
        occl = d.get("terrain_occludes")
        if occl is None:
            occl = _TERRAIN_OCCLUDES.get(stype, True)
        return cls(
            type=stype,
            range_m=float(d.get("range_m", 0.0)),
            fov_deg=float(d.get("fov_deg", 360.0)),
            pos_noise_m=float(d.get("pos_noise_m", 0.0)),
            detects=list(d.get("detects", []) or []),
            terrain_occludes=bool(occl),
        )

    def can_detect(self, category: str) -> bool:
        return category in self.detects


@dataclass
class EffectSpec:
    """An effect an equipment can deliver — feasibility + sim resolution."""

    type: str = "indirect_fire"          # indirect_fire | direct_fire | loiter_strike | jam | sam_engage | recon_reveal
    range_m: float = 0.0
    min_range_m: float = 0.0
    lethal_r: float = 0.0
    tof_s: float = 0.0                   # time-of-flight before the effect lands
    degrades: str = ""                   # for jam: comms | radar
    min_alt_m: float = 0.0               # SAM engagement envelope floor (P4)
    max_alt_m: float = float("inf")      # SAM engagement envelope ceiling (P4)

    @classmethod
    def from_dict(cls, d: dict) -> "EffectSpec":
        d = d or {}
        return cls(
            type=str(d.get("type", "indirect_fire")),
            range_m=float(d.get("range_m", 0.0)),
            min_range_m=float(d.get("min_range_m", 0.0)),
            lethal_r=float(d.get("lethal_r", 0.0)),
            tof_s=float(d.get("tof_s", 0.0)),
            degrades=str(d.get("degrades", "")),
            min_alt_m=float(d.get("min_alt_m", 0.0)),
            max_alt_m=float(d.get("max_alt_m", float("inf"))),
        )

    def in_range(self, dist_m: float) -> bool:
        return self.min_range_m <= dist_m <= self.range_m

    def in_envelope(self, dist_m: float, alt_m: float) -> bool:
        """Range AND altitude gate for an air-defense engagement (P4)."""
        return self.in_range(dist_m) and self.min_alt_m <= alt_m <= self.max_alt_m


@dataclass
class EquipmentSpec:
    """One catalog entry — a platform's full capability profile."""

    key: str
    side: str = "blue"
    domain: str = "land"
    role: str = "maneuver"
    category: str = ""                   # abstract class (armor/air_defense/...) — mainly for red
    sensors: list = field(default_factory=list)        # list[SensorSpec]
    effects: list = field(default_factory=list)        # list[EffectSpec]
    emits: dict = field(default_factory=dict)          # {type, detectable_by: [...]}
    commands: list = field(default_factory=list)       # ActionType names it accepts
    constraints: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, key: str, d: dict) -> "EquipmentSpec":
        d = d or {}
        return cls(
            key=key,
            side=str(d.get("side", "blue")),
            domain=str(d.get("domain", "land")),
            role=str(d.get("role", "maneuver")),
            category=str(d.get("category", "")),
            sensors=[SensorSpec.from_dict(s) for s in (d.get("sensors") or [])],
            effects=[EffectSpec.from_dict(e) for e in (d.get("effects") or [])],
            emits=dict(d.get("emits") or {}),
            commands=list(d.get("commands") or []),
            constraints=dict(d.get("constraints") or {}),
        )

    # ---- capability queries (used by feeds / AssetMatch / effect resolver) -- #
    def provides_sensor(self, sensor_type: str) -> Optional[SensorSpec]:
        return next((s for s in self.sensors if s.type == sensor_type), None)

    def effect(self, effect_type: str) -> Optional[EffectSpec]:
        return next((e for e in self.effects if e.type == effect_type), None)

    def detectable_by(self, feed_type: str) -> bool:
        """True if a feed of ``feed_type`` can pick this platform up via its emission."""
        return feed_type in (self.emits.get("detectable_by") or [])

    @property
    def has_ammo_limit(self) -> bool:
        return bool(self.constraints.get("ammo"))


# --------------------------------------------------------------------------- #
class Catalog:
    """Loaded equipment catalog with capability queries."""

    def __init__(self, specs: dict[str, EquipmentSpec], source: Optional[str] = None) -> None:
        self.specs = specs
        self.source = source

    # ---- lookups --------------------------------------------------------- #
    def get(self, key: str) -> Optional[EquipmentSpec]:
        return self.specs.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self.specs

    def __len__(self) -> int:
        return len(self.specs)

    def keys(self) -> list:
        return list(self.specs.keys())

    def all(self) -> list:
        return list(self.specs.values())

    def by_side(self, side: str) -> list:
        return [s for s in self.specs.values() if s.side == side]

    def blue(self) -> list:
        return self.by_side("blue")

    def red(self) -> list:
        return self.by_side("red")

    def by_role(self, role: str) -> list:
        return [s for s in self.specs.values() if s.role == role]

    def emitters(self) -> list:
        """Equipment that radiates a detectable emission (SIGINT-findable)."""
        return [s for s in self.specs.values() if s.emits]

    def commands_for(self, key: str) -> list:
        spec = self.get(key)
        return list(spec.commands) if spec else []


# --------------------------------------------------------------------------- #
def load_catalog(path: Optional[str] = None) -> Catalog:
    """Load ``equipment_catalog.yaml`` into a typed Catalog.

    Tolerant by design — a missing file or PyYAML yields an empty catalog rather
    than raising, matching the zero-setup parity of the rest of Dexia.
    """
    path = path or DEFAULT_CATALOG_PATH
    raw: dict = {}
    source = None
    if yaml is not None and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            source = path
        except Exception:
            raw = {}
    specs = {key: EquipmentSpec.from_dict(key, val) for key, val in raw.items()
             if isinstance(val, dict)}
    return Catalog(specs, source=source)


# process-wide shared catalog
_CATALOG: Optional[Catalog] = None


def get_catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = load_catalog()
    return _CATALOG
