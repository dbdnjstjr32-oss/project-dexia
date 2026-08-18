"""Feeds — per-sensor observation models (the source of fusion's disagreement).

Each feed observes the WorldState through a catalog-derived sensor: limited
coverage, position noise, and category blind spots. They deliberately see
*different, imperfect* slices, which is what makes fusion meaningful rather than
a no-op dedupe (DESIGN_AIP.md section 5.1):

    uav_eo   airborne EO/IR — precise (±5 m) but range/LOS limited
    ugs      ground acoustic field — coarse (±20 m), ground-only, no emitters
    sigint   theater-wide — finds active emitters by signal, area-only (±150 m)

A feed's reliability feeds the Track confidence model: independent corroboration
raises confidence (engine.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..physics3d import clear_los, distance3d
from ..scenario.catalog import Catalog


@dataclass
class Detection:
    """One feed's noisy report of a real entity at one tick."""

    feed: str                  # source feed id (provenance)
    category: str              # what the feed thinks it is
    position: list             # noisy [x, y] estimate
    pos_noise_m: float         # the feed's reported 1-sigma uncertainty
    reliability: float         # feed trust [0..1] -> confidence model
    truth_id: str              # ground-truth entity (eval/debug only — NOT used for association)
    tick: int


def _noisy(rng, pos, sigma: float) -> list:
    if sigma <= 0 or rng is None:
        return [float(pos[0]), float(pos[1])]
    d = rng.normal(0.0, sigma, size=2)
    return [float(pos[0] + d[0]), float(pos[1] + d[1])]


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --------------------------------------------------------------------------- #
class Feed:
    feed_id = "feed"
    reliability = 0.5

    def observe(self, world, tick: int, rng=None) -> list:  # pragma: no cover - interface
        raise NotImplementedError


class PlatformSensorFeed(Feed):
    """A feed mounted on blue platforms that carry a matching sensor (eo_ir,
    acoustic). Coverage = the sensor's range around each platform; it detects
    only the categories the sensor lists in ``detects``."""

    def __init__(self, feed_id: str, sensor_type: str, reliability: float) -> None:
        self.feed_id = feed_id
        self.sensor_type = sensor_type
        self.reliability = reliability

    def _platforms(self, world):
        out = []
        for e in world.blue:
            spec = world.catalog.get(e.cls)
            sensor = spec.provides_sensor(self.sensor_type) if spec else None
            if sensor is not None:
                out.append((e, sensor))
        return out

    def observe(self, world, tick: int, rng=None) -> list:
        platforms = self._platforms(world)
        terrain = getattr(world, "terrain", None)      # None on the legacy/flat path
        dets: list = []
        for tgt in world.red:
            for plat, sensor in platforms:
                if not sensor.can_detect(tgt.category):
                    continue
                if distance3d(plat.position, tgt.position) > sensor.range_m:
                    continue                            # 3D range (z-safe: flat == 2D)
                if (terrain is not None and sensor.terrain_occludes
                        and not clear_los(terrain, plat.position, tgt.position)):
                    continue                            # a ridge blocks the sightline (P4)
                dets.append(Detection(
                    feed=self.feed_id, category=tgt.category,
                    position=_noisy(rng, tgt.position, sensor.pos_noise_m),
                    pos_noise_m=sensor.pos_noise_m, reliability=self.reliability,
                    truth_id=tgt.entity_id, tick=tick,
                ))
                break  # one report per target per feed
        return dets


class SigintFeed(Feed):
    """Theater-wide ELINT: finds *active emitters* by their emission, but only as
    a bearing/area (large position uncertainty) and only while radiating."""

    feed_id = "sigint"
    SIGINT_NOISE_M = 150.0

    def __init__(self, reliability: float = 0.4) -> None:
        self.reliability = reliability

    def observe(self, world, tick: int, rng=None) -> list:
        dets: list = []
        for tgt in world.red:
            spec = world.catalog.get(tgt.cls)
            if not (spec and spec.emits and tgt.emitting):
                continue
            if not spec.detectable_by("sigint"):
                continue
            dets.append(Detection(
                feed=self.feed_id, category="emitter",
                position=_noisy(rng, tgt.position, self.SIGINT_NOISE_M),
                pos_noise_m=self.SIGINT_NOISE_M, reliability=self.reliability,
                truth_id=tgt.entity_id, tick=tick,
            ))
        return dets


# reliability priors per feed (independent-corroboration confidence model)
_RELIABILITY = {"uav_eo": 0.7, "ugs": 0.55, "sigint": 0.4, "gsr": 0.6}
_SENSOR_TYPE = {"uav_eo": "eo_ir", "ugs": "acoustic", "gsr": "radar"}  # gsr: ground-search radar (P4)


def build_feeds(feed_ids: list, catalog: Optional[Catalog] = None) -> list:
    """Instantiate the feeds a scenario declares (scenario.feeds)."""
    feeds: list = []
    for fid in feed_ids:
        rel = _RELIABILITY.get(fid, 0.5)
        if fid == "sigint":
            feeds.append(SigintFeed(rel))
        elif fid in _SENSOR_TYPE:
            feeds.append(PlatformSensorFeed(fid, _SENSOR_TYPE[fid], rel))
    return feeds
