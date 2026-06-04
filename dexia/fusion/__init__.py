"""dexia.fusion — multi-source sensor fusion (the AIP core verb).

Turns a heterogeneous, *imperfect* set of feeds into a single enemy common
operating picture. The honest crux (DESIGN_AIP.md section 5.1): fusion is only
real if feeds disagree — each feed observes ground truth through its own
coverage, noise, and blind spots, and the engine reconciles them into Tracks
with confidence, uncertainty, and source provenance that improve as independent
feeds corroborate.

    WorldState   ground truth (blue assets + red entities), scripted motion
    Feeds        per-sensor observation models derived from the catalog
    FusionEngine association + Track lifecycle (birth/update/coast/stale)
"""

from __future__ import annotations

from .effects import EffectEvent, EffectResolver
from .engine import FusionEngine, Track
from .feeds import Detection, SigintFeed, PlatformSensorFeed, build_feeds
from .world import Entity, WorldState

__all__ = [
    "WorldState",
    "Entity",
    "Detection",
    "PlatformSensorFeed",
    "SigintFeed",
    "build_feeds",
    "FusionEngine",
    "Track",
    "EffectResolver",
    "EffectEvent",
]
