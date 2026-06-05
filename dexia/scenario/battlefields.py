"""Battlefield catalog (Phase D) — 100 pre-designated maps across 5 regions.

A *battlefield* is a fixed, named theatre: a geographic location + map size +
region climate/terrain character. The 100 entries are split across five regions
(중동 / 한국 / 러시아 / 유럽 / 동남아), each with its own terrain shape so the
physics that follows (LOS occlusion, recon altitude, fires) feels regionally
distinct. Equipment is NOT baked in — when a battlefield is *activated* the
BattleGenerator loads it and randomly populates blue + red onto it, then the
enemy area is fogged. The list is deterministic (seeded) so ids are stable.

This is data only; turning a battlefield into a live WorldState is
``BattleGenerator.from_battlefield`` (engine side).
"""

from __future__ import annotations

import random
from typing import Optional

# region key -> climate/terrain character. ``shape`` feeds Heightfield via
# BattleGenerator: hill (mountainous/rolling), ramp (dunes/slope), flat (plains).
REGIONS = {
    "middle_east": {
        "name_ko": "중동", "climate": "arid desert (sand, dunes, high heat haze)",
        "location": [44.0, 33.0],   # ~Iraq
        "shape": {"type": "ramp", "slope": 0.012},          # open dune slope, long sightlines
        "blue": {"tb2_recon_uav": 1, "m777_howitzer": 2, "switchblade": 1},
    },
    "korea": {
        "name_ko": "한국", "climate": "temperate, steep mountainous ridges & valleys",
        "location": [127.6, 37.8],
        "shape": {"type": "hill", "peak": 620.0, "sigma": 1100.0},  # tall, steep -> LOS hard
        "blue": {"tb2_recon_uav": 1, "m777_howitzer": 2, "switchblade": 1},
    },
    "russia": {
        "name_ko": "러시아", "climate": "cold open steppe / plains, low relief",
        "location": [37.6, 55.7],
        "shape": {"type": "flat", "z0": 0.0},               # plains -> EO sees far
        "blue": {"tb2_recon_uav": 1, "m777_howitzer": 2, "m142_himars": 1},
    },
    "europe": {
        "name_ko": "유럽", "climate": "temperate rolling farmland & treelines",
        "location": [14.0, 50.0],
        "shape": {"type": "hill", "peak": 280.0, "sigma": 2100.0},  # gentle rolling
        "blue": {"tb2_recon_uav": 1, "m777_howitzer": 2, "switchblade": 1},
    },
    "southeast_asia": {
        "name_ko": "동남아", "climate": "tropical jungle, river deltas, monsoon",
        "location": [106.0, 14.0],
        "shape": {"type": "hill", "peak": 190.0, "sigma": 2600.0},  # low broad jungle hills
        "blue": {"tb2_recon_uav": 1, "switchblade": 2, "m777_howitzer": 1},
    },
}

_DIFFICULTIES = ("easy", "medium", "hard")


def _jitter(rng: random.Random, base: float, frac: float) -> float:
    return round(base * (1.0 + rng.uniform(-frac, frac)), 1)


def generate_catalog(per_region: int = 20, seed: int = 7) -> list:
    """Build the deterministic list of 100 battlefields (20 per region)."""
    rng = random.Random(seed)
    out: list = []
    for region, tmpl in REGIONS.items():
        for i in range(1, per_region + 1):
            shape = dict(tmpl["shape"])
            for k in ("peak", "sigma", "slope"):
                if k in shape:
                    shape[k] = _jitter(rng, shape[k], 0.25)
            lon, lat = tmpl["location"]
            size_m = int(rng.choice([8000, 10000, 12000, 14000]))
            out.append({
                "id": f"{region}-{i:02d}",
                "region": region,
                "name": f"{tmpl['name_ko']} {i:02d}",
                "climate": tmpl["climate"],
                "location": [round(lon + rng.uniform(-3, 3), 3),
                             round(lat + rng.uniform(-2, 2), 3)],
                "size_m": size_m,
                "terrain_shape": shape,
                "blue": dict(tmpl["blue"]),
                "difficulty": _DIFFICULTIES[(i - 1) % len(_DIFFICULTIES)],
            })
    return out


def terrain_spec(shape: dict, size_m: int) -> dict:
    """Expand a region terrain ``shape`` into a full Heightfield spec sized to the
    map (grid covers [-size/2, +size/2] on both axes at 100 m posts)."""
    dx = 100.0
    half = size_m / 2.0
    n = int(size_m / dx) + 1
    spec = {"size": n, "dx": dx, "origin": [-half, -half]}
    typ = shape.get("type", "flat")
    if typ == "hill":
        spec.update(type="hill", peak=float(shape.get("peak", 300.0)),
                    sigma=float(shape.get("sigma", 1500.0)),
                    center=[0.0, size_m * 0.1])             # ridge between the lines
    elif typ == "ramp":
        spec.update(type="ramp", slope=float(shape.get("slope", 0.02)))
    else:
        spec.update(type="flat", z0=float(shape.get("z0", 0.0)))
    return spec


_CATALOG: Optional[list] = None


def get_catalog() -> list:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = generate_catalog()
    return _CATALOG


def get_battlefield(bf_id: str) -> Optional[dict]:
    return next((b for b in get_catalog() if b["id"] == bf_id), None)
