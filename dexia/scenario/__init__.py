"""dexia.scenario — the AIP data spine: equipment catalog + scenario format.

Promotes Dexia from a single hard-coded mission into a *data-driven* wargame:
equipment is a declarative capability ontology (``equipment_catalog.yaml``) and
each mission is a portable ``scenario.yaml`` validated against it. Everything
downstream (feeds, fusion, AssetMatch, effect resolution) derives from the
catalog, so new platforms and theaters are data edits, not code.

See DESIGN_AIP.md sections 2-3.
"""

from __future__ import annotations

from .catalog import (
    Catalog,
    EffectSpec,
    EquipmentSpec,
    SensorSpec,
    load_catalog,
)
from .scenario import (
    ForceElement,
    Mission,
    Scenario,
    ScenarioError,
    load_scenario,
)

__all__ = [
    "Catalog",
    "EquipmentSpec",
    "SensorSpec",
    "EffectSpec",
    "load_catalog",
    "Scenario",
    "Mission",
    "ForceElement",
    "ScenarioError",
    "load_scenario",
]
