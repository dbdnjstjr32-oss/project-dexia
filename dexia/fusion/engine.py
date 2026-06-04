"""FusionEngine — reconcile imperfect detections into Tracks (the AIP core).

Associates each detection to an existing Track (or births a new one), then fuses
the per-feed observations into a single estimate whose **position sharpens** and
**confidence rises** as independent feeds corroborate, and **decays** when a
track goes unseen (coasting). Each Track carries its ``sources`` — object-level
data lineage: which sensors built this belief, and when it was first seen.

Confidence model: independent-OR over the latest reliability of each contributing
feed — ``1 - Π(1 - reliability_feed)``. So SIGINT alone ~0.4, but SIGINT+EO ~0.82.
Position: inverse-variance (precision) weighting, so a ±5 m EO fix dominates a
±150 m SIGINT cut and the fused uncertainty collapses toward the best sensor.

See DESIGN_AIP.md sections 5.2-5.4.
"""

from __future__ import annotations

import math
from typing import Optional

# Category compatibility for association: an "emitter" cut (SIGINT) may be the
# same real object as an "air_defense"/"ew" fix (EO), so they may merge; armor
# and infantry may not. On merge the track adopts the *more specific* label.
_COMPAT = {
    "emitter": {"emitter", "air_defense", "ew"},
    "air_defense": {"air_defense", "emitter"},
    "ew": {"ew", "emitter"},
    "armor": {"armor", "apc"},
    "apc": {"apc", "armor"},
    "infantry": {"infantry"},
}
_SPECIFICITY = {"emitter": 0, "ew": 1, "apc": 1, "infantry": 1,
                "air_defense": 2, "armor": 2}

_MIN_SIGMA = 1.0       # position-noise floor (m)
_MIN_GATE = 40.0       # association gate floor (m)


def _compatible(a: str, b: str) -> bool:
    return a == b or b in _COMPAT.get(a, {a}) or a in _COMPAT.get(b, {b})


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Track:
    """A fused belief about one enemy entity. ``_obs`` holds the latest detection
    per feed; every recompute derives position/uncertainty/confidence/category
    from those, so corroboration is reflected immediately."""

    def __init__(self, track_id: str, det) -> None:
        self.track_id = track_id
        self._obs: dict = {}
        self.first_tick = det.tick
        self.last_seen_tick = det.tick
        self.status = "active"            # active | coasting | stale
        self.category = det.category
        self.position = list(det.position)
        self.uncertainty_r = max(det.pos_noise_m, _MIN_SIGMA)
        self.confidence = 0.0
        self._hist: list = []             # [(tick, [x,y])] for velocity estimation
        self.add(det)

    # ---- update ---------------------------------------------------------- #
    def add(self, det) -> None:
        self._obs[det.feed] = det
        self.last_seen_tick = det.tick
        self.status = "active"
        self._recompute()
        # one history sample per tick (latest fused estimate) for velocity
        if self._hist and self._hist[-1][0] == det.tick:
            self._hist[-1] = (det.tick, list(self.position))
        else:
            self._hist.append((det.tick, list(self.position)))
            if len(self._hist) > 6:
                self._hist.pop(0)

    def _recompute(self) -> None:
        obs = list(self._obs.values())
        # category: most specific label any feed has asserted
        self.category = max((o.category for o in obs),
                            key=lambda c: _SPECIFICITY.get(c, 0))
        # position: inverse-variance weighted mean; uncertainty = combined sigma
        wsum = 0.0
        px = py = 0.0
        for o in obs:
            w = 1.0 / (max(o.pos_noise_m, _MIN_SIGMA) ** 2)
            wsum += w
            px += w * o.position[0]
            py += w * o.position[1]
        self.position = [px / wsum, py / wsum]
        self.uncertainty_r = round(math.sqrt(1.0 / wsum), 2)
        # confidence: independent-OR of contributing feeds' reliabilities
        prod = 1.0
        for o in obs:
            prod *= (1.0 - max(0.0, min(1.0, o.reliability)))
        self.confidence = round(1.0 - prod, 3)

    def coast(self, decay: float) -> None:
        self.status = "coasting"
        self.confidence = round(self.confidence * decay, 3)

    @property
    def sources(self) -> list:
        return sorted(self._obs.keys())

    @property
    def velocity(self) -> list:
        """Estimated [vx, vy] m/s from the fused-position history (noisy)."""
        if len(self._hist) < 2:
            return [0.0, 0.0]
        (t0, p0), (t1, p1) = self._hist[0], self._hist[-1]
        dt = t1 - t0
        if dt <= 0:
            return [0.0, 0.0]
        return [(p1[0] - p0[0]) / dt, (p1[1] - p0[1]) / dt]

    def predicted(self, lead_s: float) -> list:
        """Where the track will be in ``lead_s`` seconds — the aimpoint for a
        weapon with that time-of-flight (lead a moving target)."""
        v = self.velocity
        return [self.position[0] + v[0] * lead_s, self.position[1] + v[1] * lead_s]

    def to_dict(self) -> dict:
        v = self.velocity
        return {
            "track_id": self.track_id,
            "category": self.category,
            "position": [round(self.position[0], 1), round(self.position[1], 1)],
            "velocity": [round(v[0], 1), round(v[1], 1)],
            "uncertainty_r": self.uncertainty_r,
            "confidence": self.confidence,
            "sources": self.sources,
            "status": self.status,
            "first_tick": self.first_tick,
            "last_seen_tick": self.last_seen_tick,
        }


class FusionEngine:
    """Stateful track store + association. Persists tracks across ticks (memory),
    unlike the per-tick replace of the friendly object registry."""

    def __init__(self, *, gate_scale: float = 3.0, coast_ticks: int = 5,
                 decay: float = 0.85, drop: float = 0.15) -> None:
        self.tracks: list = []
        self.gate_scale = gate_scale
        self.coast_ticks = coast_ticks
        self.decay = decay
        self.drop = drop
        self._next = 0

    # ---- main update ----------------------------------------------------- #
    def update(self, detections: list, tick: int) -> list:
        seen: set = set()
        for det in detections:
            t = self._associate(det)
            if t is None:
                t = Track(self._new_id(det.category), det)
                self.tracks.append(t)
            else:
                t.add(det)
            seen.add(t.track_id)
        # coast everything not corroborated this tick
        for t in self.tracks:
            if t.track_id in seen or t.status == "stale":
                continue
            if tick - t.last_seen_tick > self.coast_ticks:
                t.coast(self.decay)
                if t.confidence < self.drop:
                    t.status = "stale"
        return self.active_tracks()

    def _associate(self, det) -> Optional[Track]:
        best, best_d = None, float("inf")
        for t in self.tracks:
            if t.status == "stale":
                continue
            if not _compatible(det.category, t.category):
                continue
            # combine the detection and track uncertainties in quadrature so a
            # noisy single-source track (±150 m SIGINT) doesn't fragment into
            # duplicates as its cuts scatter, while a sharp multi-source track
            # keeps a tight gate.
            sigma = math.hypot(det.pos_noise_m, t.uncertainty_r)
            gate = self.gate_scale * max(sigma, _MIN_GATE)
            d = _dist(det.position, t.position)
            if d <= gate and d < best_d:
                best, best_d = t, d
        return best

    def _new_id(self, category: str) -> str:
        self._next += 1
        return f"TRK-{category[:3].upper()}-{self._next:03d}"

    # ---- views ----------------------------------------------------------- #
    def active_tracks(self) -> list:
        return [t for t in self.tracks if t.status != "stale"]

    def snapshot(self) -> list:
        """All non-stale tracks as plain dicts (for OAG / registry merge)."""
        return [t.to_dict() for t in self.active_tracks()]
