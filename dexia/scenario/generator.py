"""Scenario generator — theater templates -> a validated library (toward 100).

The engine is built; scenarios are now *data we mint*, not code. Each theater
template declares plausible blue packages and red orders of battle (with count
ranges and behaviours); the generator samples them deterministically (seeded),
lays out a coherent geometry (red advancing from the east, blue in depth, an
air-defence threat often placed beyond initial EO range so the agent must
collect), validates against the catalog, and writes a portable scenario YAML.

The resulting library doubles as the eval suite (DESIGN_AIP.md 8.3, build #6).

CLI:  python -m dexia.scenario.generator --count 100 --out scenarios/generated
"""

from __future__ import annotations

import argparse
import os
import random
from typing import Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from .catalog import Catalog, get_catalog
from .scenario import SCENARIO_DIR, Scenario

GENERATED_DIR = os.path.join(SCENARIO_DIR, "generated")

# theater -> declarative template. (cls, (min,max)[, behavior]) tuples; behavior
# defaults vary by role. tasking is the operator intent free-text.
TEMPLATES: dict = {
    "eastern_europe": {
        "tasking": "동측에서 진격하는 적 기갑부대를 저지선까지 막아라. 아군 손실 최소, 부수피해 회피.",
        "intents": ["delay", "deny", "destroy"],
        "feeds": ["sigint", "ugs", "uav_eo"],
        "blue": [("m777_howitzer", 1, 2), ("tb2_recon_uav", 1, 1),
                 ("switchblade", 2, 4), ("ew_jammer_gnd", 0, 1), ("ugs_field", 2, 3)],
        "red_advance": [("t72_tank", 4, 8), ("bmp_apc", 2, 4)],
        "red_air_defense": [("sa11_sam", 1, 1), ("krasukha_ew", 0, 1)],
    },
    "middle_east": {
        "tasking": "와디를 따라 침투하는 적 기술차량·보병 습격조를 거부하라. 민간지역 교전 금지.",
        "intents": ["deny", "destroy", "seize"],
        "feeds": ["ugs", "uav_eo"],
        "geom": {"ax": (3000, 4500), "advance": (1500, 2500)},   # close infiltration fight
        # loitering munitions (no min-range, terminal homing) suit the close fight
        "blue": [("recon_drone", 1, 2), ("kami_drone", 3, 6),
                 ("switchblade", 2, 4), ("ugs_field", 2, 3)],
        "red_advance": [("technical_truck", 3, 5), ("infantry_squad", 2, 3)],
        "red_air_defense": [("manpads_team", 0, 2)],
    },
    "korea": {
        "tasking": "해안 접근로로 밀고 들어오는 적 기계화부대와 장사정포를 지연·무력화하라.",
        "intents": ["delay", "deny", "destroy"],
        "feeds": ["sigint", "ugs", "uav_eo"],
        # close-support gun (m777) for the near fight + HIMARS for depth
        "blue": [("m777_howitzer", 1, 2), ("switchblade", 2, 4),
                 ("m142_himars", 0, 1), ("tb2_recon_uav", 1, 1), ("ew_jammer_gnd", 0, 1)],
        "red_advance": [("t72_tank", 3, 6), ("bmp_apc", 2, 4)],
        "red_air_defense": [("koksan_170mm", 1, 2), ("sa11_sam", 0, 1)],
    },
    "desert_storm": {
        "tasking": "통합방공망 아래 전개한 적 기갑사단을 SEAD 후 격멸하라.",
        "intents": ["destroy", "deny"],
        "feeds": ["sigint", "ugs", "uav_eo"],
        "blue": [("m142_himars", 1, 2), ("m777_howitzer", 1, 2), ("tb2_recon_uav", 1, 1),
                 ("switchblade", 2, 4), ("ew_jammer_gnd", 1, 1)],
        "red_advance": [("t72_tank", 6, 10), ("bmp_apc", 3, 5)],
        "red_air_defense": [("s300_sam", 1, 1), ("sa11_sam", 0, 1), ("krasukha_ew", 0, 1)],
    },
}

_AD_BEHAVIOR = {"krasukha_ew": "periodic_jam"}   # else static_ad


def _count(rng, lo, hi) -> int:
    return rng.randint(lo, hi)


def generate_scenario_dict(theater: str, idx: int, *, seed: int = 0) -> dict:
    """Build one scenario as a plain YAML-ready dict (deterministic in seed+idx)."""
    tpl = TEMPLATES[theater]
    rng = random.Random((seed << 16) ^ (hash(theater) & 0xFFFF) ^ idx)

    geom = tpl.get("geom", {})
    ax = rng.randint(*geom.get("ax", (5000, 7000)))     # red advance start (east)
    ex = ax - rng.randint(*geom.get("advance", (2500, 4000)))   # advance objective
    hold_line = ax - rng.randint(800, 1500)      # red must not cross this
    bx = -rng.randint(800, 1600)                 # blue depth (west)

    blue = []
    for cls, lo, hi in tpl["blue"]:
        n = _count(rng, lo, hi)
        if n >= 1:
            blue.append({"cls": cls, "n": n, "pos": [bx, rng.randint(-200, 200)]})

    red = []
    for cls, lo, hi in tpl["red_advance"]:
        n = _count(rng, lo, hi)
        if n >= 1:
            ay = rng.randint(-300, 300)
            red.append({"cls": cls, "n": n, "behavior": "advance",
                        "route": [[ax, ay], [ex, ay]]})
    for cls, lo, hi in tpl["red_air_defense"]:
        n = _count(rng, lo, hi)
        if n >= 1:
            # placed laterally, often beyond initial EO range -> agent must collect
            side = rng.choice([-1, 1])
            red.append({"cls": cls, "n": n,
                        "behavior": _AD_BEHAVIOR.get(cls, "static_ad"),
                        "pos": [rng.randint(5500, 8000), side * rng.randint(1500, 2800)]})

    return {"scenario": {
        "id": f"{theater}-{idx:03d}",
        "theater": theater,
        "mission": {
            "tasking": tpl["tasking"],
            "intent": rng.choice(tpl["intents"]),
            "roe": ["no_civilian_area"] if theater == "middle_east" else [],
            "victory": {"hold_line": [hold_line, 0],
                        "max_blue_loss": rng.randint(1, 3),
                        "time_limit_s": rng.choice([480, 600, 720])},
        },
        "blue": blue,
        "red": red,
        "feeds": list(tpl["feeds"]),
    }}


def generate_scenario(theater: str, idx: int, *, seed: int = 0,
                      catalog: Optional[Catalog] = None) -> Scenario:
    sc = Scenario.from_dict(generate_scenario_dict(theater, idx, seed=seed))
    return sc.require_valid(catalog)


def generate_library(count: int = 100, out_dir: str = GENERATED_DIR, *,
                     seed: int = 0, catalog: Optional[Catalog] = None) -> list:
    """Mint ``count`` validated scenarios round-robin across theaters; write YAML.
    Returns the written file paths. Raises if any scenario fails validation."""
    if yaml is None:
        raise RuntimeError("PyYAML not available — cannot write scenarios")
    catalog = catalog or get_catalog()
    os.makedirs(out_dir, exist_ok=True)
    theaters = list(TEMPLATES.keys())
    paths: list = []
    per: dict = {t: 0 for t in theaters}
    for i in range(count):
        theater = theaters[i % len(theaters)]
        per[theater] += 1
        d = generate_scenario_dict(theater, per[theater], seed=seed)
        # validate before writing — the generator's quality gate
        problems = Scenario.from_dict(d).validate(catalog)
        if problems:                              # pragma: no cover - defensive
            raise RuntimeError(f"generated {d['scenario']['id']} invalid: {problems}")
        path = os.path.join(out_dir, d["scenario"]["id"] + ".yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(d, f, allow_unicode=True, sort_keys=False, width=100)
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate a Dexia scenario library.")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--out", default=GENERATED_DIR)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    paths = generate_library(args.count, args.out, seed=args.seed)
    by_theater: dict = {}
    for p in paths:
        t = os.path.basename(p).rsplit("-", 1)[0]
        by_theater[t] = by_theater.get(t, 0) + 1
    print(f"generated {len(paths)} scenarios -> {args.out}")
    for t, n in sorted(by_theater.items()):
        print(f"  {t:<16} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
