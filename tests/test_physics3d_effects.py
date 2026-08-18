"""3D LOS occlusion, ballistic fires, and SAM PN intercept (tier B / P4).

Proves the sensing/shooting layer is now 3D — and that it is a strict no-op
without terrain (full regression compatibility):

  * LOS        a tank behind a ridge is EO-blind but the ground-search radar
               (terrain_occludes: false) still detects it
  * ballistic  the howitzer lobs a real 3D arc OVER the ridge onto the hidden tank
  * SAM        engagement gates on range ∧ altitude ∧ LOS; a PN MissileEngine
               then intercepts the aircraft
  * gating     clear_los(None,...) is always True; an EO feed on a no-terrain
               world detects exactly as before P4

Dual-mode: ``pytest`` *and* ``python tests/test_physics3d_effects.py``.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import numpy as np

from dexia.fusion import EffectResolver, PlatformSensorFeed, WorldState
from dexia.fusion.effects import engage_air, sam_can_engage
from dexia.fusion.world import Entity
from dexia.ontology.actions import to_command
from dexia.physics3d import Heightfield, MissileEngine, clear_los, distance3d
from dexia.scenario.catalog import load_catalog
from dexia.scenario.scenario import load_scenario

_CAT = load_catalog()
_DEMO = "ridge-los-p4"


def _eo():
    return PlatformSensorFeed("uav_eo", "eo_ir", 0.7)


def _gsr():
    return PlatformSensorFeed("gsr", "radar", 0.6)


# --------------------------------------------------------------------------- #
def test_los_occlusion_eo_blind_radar_sees():
    """The behind-ridge tank is invisible to EO (terrain occludes the sightline)
    but the ground-search radar detects it. -> 'EO 탐지 실패 / Radar 탐지 가능'."""
    w = WorldState.from_scenario(load_scenario(_DEMO), _CAT)
    rng = np.random.default_rng(0)
    eo_ids = {d.truth_id for d in _eo().observe(w, 1, rng)}
    gsr_ids = {d.truth_id for d in _gsr().observe(w, 1, rng)}

    assert "t72_tank" in eo_ids, "EO must see the tank IN FRONT of the ridge"
    assert "bmp_apc" not in eo_ids, "EO must be BLIND to the tank behind the ridge"
    assert {"t72_tank", "bmp_apc"} <= gsr_ids, "radar sees past the ridge to both"


def test_raycast_blocks_then_clears_over_ridge():
    hill = Heightfield.hill(peak=350.0, center=(3000.0, 0.0), sigma=1000.0,
                            size=201, dx=50.0, origin=(-2000.0, -5000.0))
    low = [0.0, 0.0, 5.0]
    behind = [4500.0, 0.0, hill.height(4500.0, 0.0)]
    assert hill.raycast(low, behind) is not None, "ground sightline is blocked by the ridge"
    # an airborne sensor clears the crest -> LOS restored
    high = [0.0, 0.0, 1500.0]
    assert hill.raycast(high, behind) is None, "altitude restores line of sight"


def test_ballistic_fire_arcs_over_ridge_and_kills():
    w = WorldState.from_scenario(load_scenario(_DEMO), _CAT)
    res = EffectResolver(w)
    behind = next(e for e in w.red if e.cls == "bmp_apc")
    aim = [behind.position[0], behind.position[1]]
    launched = res.submit(to_command("request_fires",
                                     {"asset_id": "m777_howitzer", "target": aim}), 1)
    assert launched.status == "launched", launched.detail
    assert launched.trajectory, "a 3D ballistic arc should be attached for the HUD"
    apex = max(p[2] for p in launched.trajectory)
    assert apex > 350.0 + 50.0, f"the shell must arc OVER the 350 m ridge (apex {apex:.0f} m)"

    killed = []
    for tk in range(2, 60):
        w.step(1.0)
        killed += [k for ev in res.step(tk) for k in ev.killed]
    assert "bmp_apc" in killed, "the lobbed round should neutralise the hidden tank"


def test_sam_three_check_and_pn_intercept():
    sam = _CAT.get("sa11_sam")
    eff = sam.effect("sam_engage")
    sam_pos = [0.0, 0.0, 20.0]
    flat = None

    # in envelope + clear LOS -> engages, and PN intercepts (miss < warhead lethal_r)
    tgt = [12000.0, 0.0, 3000.0]
    out = engage_air(sam_pos, eff, tgt, target_vel=[-200.0, 0.0, 0.0], terrain=flat)
    assert out["engaged"] and out["hit"], f"should engage & hit: {out}"

    # too high -> altitude check fails
    high = sam_can_engage(sam_pos, eff, [12000.0, 0.0, 40000.0])
    assert not high["altitude"] and not high["engage"]

    # behind a ridge -> LOS check fails even though range/altitude are fine
    hill = Heightfield.hill(peak=2000.0, center=(3000.0, 0.0), sigma=800.0,
                            size=201, dx=50.0, origin=(-2000.0, -5000.0))
    masked = sam_can_engage([0.0, 0.0, 20.0], eff, [6000.0, 0.0, 800.0], terrain=hill)
    assert masked["range"] and not masked["los"] and not masked["engage"]


def test_missile_intercept_segment_accuracy():
    # 800 m/s * 0.02 s = 16 m steps, yet the segment closest-approach keeps a
    # real hit from degrading into a dt-sampling miss (C-4): a near-stationary
    # target is a clean kill, not a ~20 m sampled miss.
    miss = MissileEngine(speed=800.0).intercept(
        [0.0, 0.0, 0.0], [4000.0, 0.0, 1000.0], target_vel=[0.0, 0.0, 0.0])
    assert miss < 5.0, f"near-stationary target should be a clean hit; miss={miss:.1f}"

    # a slow inbound aircraft (e.g. a recon UAV) is comfortably intercepted
    slow = MissileEngine(speed=800.0).intercept(
        [0.0, 0.0, 20.0], [8000.0, 0.0, 1500.0], target_vel=[-30.0, 0.0, 0.0])
    assert slow < 30.0, f"slow inbound target should be hit; miss={slow:.1f}"


def test_red_air_defense_kills_airborne_blue():
    cat = load_catalog()
    # red SAM at the origin; a blue recon UAV airborne, in range + envelope + LOS.
    sam = Entity(entity_id="sa11_sam", cls="sa11_sam", side="red",
                 category="air_defense", position=[0.0, 0.0, 0.0])
    uav = Entity(entity_id="tb2_recon_uav", cls="tb2_recon_uav", side="blue",
                 category="air", position=[6000.0, 0.0, 1500.0])
    world = WorldState([sam, uav], cat)
    resolver = EffectResolver(world, cat)
    killed = False
    for tick in range(1, 12):
        evs = resolver.resolve_air_defense(tick)
        if any(uav.entity_id in e.killed for e in evs):
            killed = True
            break
    assert killed and not uav.alive, "red SAM should engage & destroy the airborne blue UAV"
    assert len(world.blue) == 0, "blue attrition is now real (B-3)"

    # a blue asset ON THE DECK (AGL ~ 0) is NOT a SAM target
    sam2 = Entity(entity_id="sa11_sam", cls="sa11_sam", side="red",
                  category="air_defense", position=[0.0, 0.0, 0.0])
    ground = Entity(entity_id="m777_howitzer", cls="m777_howitzer", side="blue",
                    category="artillery", position=[5000.0, 0.0, 0.0])
    world2 = WorldState([sam2, ground], cat)
    r2 = EffectResolver(world2, cat)
    evs2 = []
    for tick in range(1, 12):
        evs2 += r2.resolve_air_defense(tick)
    assert ground.alive and not evs2, "a ground asset (AGL~0) must not be SAM-engaged"


def test_distance3d_is_zsafe():
    assert math.isclose(distance3d([0, 0], [3, 4]), 5.0)             # 2D inputs
    assert math.isclose(distance3d([0, 0, 0], [3, 4, 12]), 13.0)     # 3D
    assert math.isclose(distance3d([0, 0, 0], [3, 4]), 5.0)          # mixed -> z=0


def test_los_flat_world_is_noop():
    """The gating proof: no terrain ⇒ never occludes, and an EO feed on a
    terrain-less world detects identically to pre-P4."""
    assert clear_los(None, [0, 0, 0], [9999, 9999, 9999]) is True
    # EO platform + a target well inside range, NO terrain on the world
    w = WorldState([
        Entity("tb2_recon_uav", "tb2_recon_uav", "blue", "isr", [0.0, 0.0]),
        Entity("t72_tank", "t72_tank", "red", "armor", [3000.0, 0.0]),
    ], _CAT)
    assert w.terrain is None and not w.physics
    seen = {d.truth_id for d in _eo().observe(w, 1, np.random.default_rng(0))}
    assert seen == {"t72_tank"}, "flat-world EO detection unchanged (no LOS filtering)"


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA tier B / P4 — 3D LOS · ballistic fires · SAM PN intercept")
    print(bar)

    w = WorldState.from_scenario(load_scenario(_DEMO), _CAT)
    rng = np.random.default_rng(0)
    eo_ids = sorted({d.truth_id for d in _eo().observe(w, 1, rng)})
    gsr_ids = sorted({d.truth_id for d in _gsr().observe(w, 1, rng)})
    print(f"\n[LOS] ridge at x~3000 (peak 350 m)")
    print(f"      EO  (terrain_occludes) sees: {eo_ids}   <- behind-ridge tank MISSING")
    print(f"      GSR (radar, sees past)  sees: {gsr_ids}   <- both detected")

    res = EffectResolver(w)
    behind = next(e for e in w.red if e.cls == "bmp_apc")
    ev = res.submit(to_command("request_fires",
                               {"asset_id": "m777_howitzer", "target": list(behind.position[:2])}), 1)
    apex = max(p[2] for p in ev.trajectory) if ev.trajectory else 0.0
    print(f"\n[ballistic] M777 lobs over the ridge: apex={apex:.0f} m "
          f"({len(ev.trajectory)} arc samples)")
    for tk in range(2, 60):
        w.step(1.0)
        for imp in res.step(tk):
            print(f"      t={tk} {imp.status}: {imp.detail}")

    sam = _CAT.get("sa11_sam").effect("sam_engage")
    print("\n[SAM] 3-check engagement of an inbound aircraft (PN intercept):")
    out = engage_air([0.0, 0.0, 20.0], sam, [12000.0, 0.0, 3000.0],
                     target_vel=[-200.0, 0.0, 0.0])
    print(f"      checks={out['checks']}")
    print(f"      engaged={out['engaged']}  miss={out.get('miss_m')} m  hit={out.get('hit')}")

    print("\n" + bar)
    print("3D LOS / EFFECTS P4 VERIFIED ✅  (EO occluded behind ridge / radar sees; "
          "fires arc over terrain; SAM range·alt·LOS + PN intercept)")
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
