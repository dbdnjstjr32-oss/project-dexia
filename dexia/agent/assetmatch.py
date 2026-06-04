"""AssetMatch — deterministic feasibility matrix (the AIP "logic" the AI shows).

For every fused enemy track x every blue asset, decide whether that asset can
service that track and how good a choice it is — grounded in the catalog's effect
specs (range, ammo, time-of-flight) so the LLM downstream *prioritises* rather
than *invents* feasibility. This is the transparent reasoning substrate behind
"the AI chose the right asset" (DESIGN_AIP.md 6.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

GROUND = {"armor", "apc", "infantry", "artillery"}
EMITTERISH = {"emitter", "air_defense", "ew"}
_THREAT = {"air_defense": 1.0, "emitter": 0.9, "ew": 0.8,
           "armor": 0.7, "apc": 0.6, "artillery": 0.6, "infantry": 0.5}


@dataclass
class Option:
    track_id: str
    asset_id: str
    cmd: str                              # request_fires | engage | jam | task_isr
    feasible: bool
    score: float
    reason: str
    target: list = field(default_factory=list)   # aimpoint (lead for moving targets)

    def to_dict(self) -> dict:
        return {"asset": self.asset_id, "cmd": self.cmd, "feasible": self.feasible,
                "score": self.score, "reason": self.reason}


def _dist(a, b) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _score(track, dist: float, rng_max: float) -> float:
    prox = max(0.0, 1.0 - dist / max(rng_max, 1.0))
    threat = _THREAT.get(track.category, 0.5)
    return round(0.5 * threat + 0.3 * prox + 0.2 * float(track.confidence), 3)


def _options_for(track, asset, spec, confirm_threshold: float) -> list:
    opts: list = []

    fires = spec.effect("indirect_fire")
    if fires and track.category in GROUND:
        aim = track.predicted(fires.tof_s)         # lead the moving column
        d = _dist(asset.position, aim)
        feasible = fires.in_range(d) and asset.ammo > 0
        reason = ("in range, ammo {}".format(int(asset.ammo)) if feasible
                  else ("winchester" if asset.ammo <= 0 else f"range {int(d)}m"))
        opts.append(Option(track.track_id, asset.entity_id, "request_fires",
                           feasible, _score(track, d, fires.range_m), reason, aim))

    strike = spec.effect("loiter_strike")
    if strike and track.category in GROUND:
        aim = track.predicted(strike.tof_s)
        d = _dist(asset.position, aim)
        feasible = d <= strike.range_m and asset.ammo > 0
        opts.append(Option(track.track_id, asset.entity_id, "engage",
                           feasible, _score(track, d, strike.range_m),
                           "reachable" if feasible else f"range {int(d)}m", aim))

    jam = spec.effect("jam")
    if jam and track.category in EMITTERISH:
        d = _dist(asset.position, track.position)
        feasible = d <= jam.range_m
        opts.append(Option(track.track_id, asset.entity_id, "jam",
                           feasible, _score(track, d, jam.range_m),
                           "in jam range" if feasible else f"range {int(d)}m",
                           list(track.position)))

    # ISR: a repositionable sensor asset can be tasked to confirm a vague track
    if spec.role == "isr" and not spec.constraints.get("static") and \
            track.confidence < confirm_threshold:
        opts.append(Option(track.track_id, asset.entity_id, "task_isr",
                           True, round(0.4 + (confirm_threshold - track.confidence), 3),
                           f"confirm conf {track.confidence}", list(track.position)))
    return opts


def match_assets(tracks, assets, catalog, *, confirm_threshold: float = 0.6) -> dict:
    """Return {track_id: [Option, ...]} sorted best-first (feasible, then score)."""
    out: dict = {}
    for tr in tracks:
        opts: list = []
        for a in assets:
            if not getattr(a, "alive", True):
                continue
            spec = catalog.get(a.cls)
            if spec is None:
                continue
            opts += _options_for(tr, a, spec, confirm_threshold)
        opts.sort(key=lambda o: (o.feasible, o.score), reverse=True)
        out[tr.track_id] = opts
    return out
