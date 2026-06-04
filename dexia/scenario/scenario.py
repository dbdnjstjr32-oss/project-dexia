"""Scenario — a portable, declarative mission (AIP data spine).

A scenario is data: theater + operator intent + blue/red force laydown +
available feeds + victory conditions. Validated against the equipment catalog so
a generated or hand-authored mission is provably runnable before it loads.

The library of these (``scenarios/*.yaml``) is both the wargame content AND the
eval suite — scoring whether the AI actually solved each one. See DESIGN_AIP.md
section 3.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from .catalog import Catalog, get_catalog

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCENARIO_DIR = os.path.join(_ROOT, "scenarios")

INTENTS = ("deny", "destroy", "delay", "recon", "seize")
KNOWN_FEEDS = ("sigint", "ugs", "uav_eo", "gsr")    # gsr: ground-search radar (P4)


class ScenarioError(Exception):
    """Raised when a scenario fails validation against the catalog."""


# --------------------------------------------------------------------------- #
@dataclass
class ForceElement:
    """A unit (or group of ``n``) of one equipment type on the field."""

    cls: str                              # equipment catalog key
    n: int = 1
    pos: Optional[list] = None            # [x, y] local metres
    route: Optional[list] = None          # [[x,y], ...] waypoints (mobile entities)
    behavior: str = "static"              # static | advance | static_ad | periodic_jam | ...

    @classmethod
    def from_dict(cls, d: dict) -> "ForceElement":
        d = d or {}
        return cls(
            cls=str(d.get("cls", d.get("class", ""))),
            n=int(d.get("n", 1)),
            pos=list(d["pos"]) if d.get("pos") is not None else None,
            route=[list(p) for p in d["route"]] if d.get("route") else None,
            behavior=str(d.get("behavior", "static")),
        )


@dataclass
class Mission:
    tasking: str = ""                     # operator intent, free text (drives the LLM)
    intent: str = "deny"                  # deny | destroy | delay | recon | seize
    roe: list = field(default_factory=list)
    victory: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Mission":
        d = d or {}
        return cls(
            tasking=str(d.get("tasking", "")),
            intent=str(d.get("intent", "deny")),
            roe=list(d.get("roe", []) or []),
            victory=dict(d.get("victory", {}) or {}),
        )


@dataclass
class Scenario:
    id: str
    theater: str = "generic"
    mission: Mission = field(default_factory=Mission)
    blue: list = field(default_factory=list)      # list[ForceElement]
    red: list = field(default_factory=list)       # list[ForceElement]
    feeds: list = field(default_factory=list)
    terrain: Optional[dict] = None                # opt-in 3D heightfield (tier B / P3)
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict, source: Optional[str] = None) -> "Scenario":
        # accept both {scenario: {...}} and a bare {...}
        body = d.get("scenario", d) if isinstance(d, dict) else {}
        terrain = body.get("terrain")
        return cls(
            id=str(body.get("id", "unnamed")),
            theater=str(body.get("theater", "generic")),
            mission=Mission.from_dict(body.get("mission", {})),
            blue=[ForceElement.from_dict(e) for e in (body.get("blue") or [])],
            red=[ForceElement.from_dict(e) for e in (body.get("red") or [])],
            feeds=list(body.get("feeds", []) or []),
            terrain=dict(terrain) if isinstance(terrain, dict) else None,
            source=source,
        )

    def all_forces(self) -> list:
        return list(self.blue) + list(self.red)

    # ---- validation ------------------------------------------------------ #
    def validate(self, catalog: Optional[Catalog] = None) -> list[str]:
        """Return a list of problems (empty == valid). Checks every force's
        equipment exists in the catalog, sides match, feeds and intent are known,
        and victory conditions are present."""
        catalog = catalog or get_catalog()
        problems: list[str] = []

        if self.mission.intent not in INTENTS:
            problems.append(f"unknown intent '{self.mission.intent}' (expected {INTENTS})")
        if not self.mission.victory:
            problems.append("mission.victory is empty — no win condition to evaluate")
        for f in self.feeds:
            if f not in KNOWN_FEEDS:
                problems.append(f"unknown feed '{f}' (expected {KNOWN_FEEDS})")

        for side, forces in (("blue", self.blue), ("red", self.red)):
            for fe in forces:
                spec = catalog.get(fe.cls)
                if spec is None:
                    problems.append(f"{side}: equipment '{fe.cls}' not in catalog")
                    continue
                if spec.side != side:
                    problems.append(
                        f"{side}: '{fe.cls}' is a {spec.side} platform on the {side} force")
                if fe.n < 1:
                    problems.append(f"{side}: '{fe.cls}' has n={fe.n} (<1)")
        return problems

    def require_valid(self, catalog: Optional[Catalog] = None) -> "Scenario":
        problems = self.validate(catalog)
        if problems:
            raise ScenarioError(
                f"scenario '{self.id}' invalid:\n  - " + "\n  - ".join(problems))
        return self


# --------------------------------------------------------------------------- #
def load_scenario(path: str, *, validate: bool = True,
                  catalog: Optional[Catalog] = None) -> Scenario:
    """Load one scenario YAML. Raises ScenarioError if ``validate`` and invalid."""
    if yaml is None:
        raise ScenarioError("PyYAML not available — cannot load scenarios")
    if not os.path.isabs(path):
        # accept a bare id or filename relative to the scenarios dir
        cand = path if path.endswith((".yaml", ".yml")) else f"{path}.yaml"
        path = cand if os.path.exists(cand) else os.path.join(SCENARIO_DIR, cand)
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    sc = Scenario.from_dict(raw, source=path)
    return sc.require_valid(catalog) if validate else sc


def list_scenarios(directory: Optional[str] = None) -> list[str]:
    """Scenario file paths in the library directory."""
    directory = directory or SCENARIO_DIR
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f) for f in os.listdir(directory)
        if f.endswith((".yaml", ".yml"))
    )
