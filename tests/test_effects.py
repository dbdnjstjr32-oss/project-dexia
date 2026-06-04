"""Effect resolution + asset-tasking actions (AIP build #3).

Proves the AI can operate *real, diverse equipment* through the governed funnel
(C5): the new ActionTypes (request_fires / task_isr / jam) auto-surface as LLM
tools and route through the ActionBus, and the EffectResolver applies their
catalog-defined effects to the world —

  * request_fires -> rounds in flight (tof) -> armor neutralised inside lethal_r
  * jam           -> enemy SAM radar forced off (SIGINT loses it)
  * task_isr      -> ISR drone repositions, then its feed confirms a hidden track

Dual-mode: ``pytest`` *and* ``python tests/test_effects.py``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import numpy as np

from dexia.fusion import EffectResolver, PlatformSensorFeed, SigintFeed, WorldState
from dexia.fusion.world import Entity
from dexia.ontology.actions import ACTION_REGISTRY, ollama_tools, to_command
from dexia.scenario import load_catalog

_CAT = load_catalog()


# --------------------------------------------------------------------------- #
def test_new_actions_auto_surface_as_tools():
    """Registering the ActionTypes is enough — the LLM tool list and the command
    mapping both derive from the one registry (no drift)."""
    for name in ("request_fires", "task_isr", "jam"):
        assert name in ACTION_REGISTRY
    tool_names = {t["function"]["name"] for t in ollama_tools()}
    assert {"request_fires", "task_isr", "jam"} <= tool_names
    # clearance: lethal fires are commander-gated, ISR/jam operator-level
    assert ACTION_REGISTRY["request_fires"].required_clearance == "commander"
    assert ACTION_REGISTRY["task_isr"].required_clearance == "operator"


def test_capability_guard_rejects_wrong_asset():
    """An ActionType guard derived from the catalog: you cannot request fires
    from a drone that has no indirect_fire effect."""
    from dexia.ontology.actions import ACTION_REGISTRY as R, ActionRejected
    rf = R["request_fires"]
    # a real battery passes the capability guard
    rf.validate(None, "m777_howitzer", {"asset_id": "m777_howitzer", "x": 5000, "y": 0})
    # a recon drone does not
    try:
        rf.validate(None, "tb2_recon_uav", {"asset_id": "tb2_recon_uav", "x": 5000, "y": 0})
        raised = False
    except ActionRejected:
        raised = True
    assert raised, "request_fires from a non-fires asset must be rejected"


def test_request_fires_neutralises_armor():
    world = WorldState([
        Entity("m777_0", "m777_howitzer", "blue", "fires", [-1000, 0], ammo=10),
        Entity("t72_0", "t72_tank", "red", "armor", [5000, 0]),
        Entity("t72_1", "t72_tank", "red", "armor", [5030, 0]),
        Entity("t72_far", "t72_tank", "red", "armor", [5300, 0]),
    ], _CAT)
    res = EffectResolver(world)
    cmd = to_command("request_fires", {"asset_id": "m777_0", "target": [5015, 0]})
    launched = res.submit(cmd, tick=1)
    assert launched.status == "launched" and world.get("m777_0").ammo == 9

    impacts = []
    for tk in range(2, 60):
        world.step(1.0)
        impacts += res.step(tk)
    killed = {k for ev in impacts for k in ev.killed}
    assert killed == {"t72_0", "t72_1"}            # both inside 50 m lethal_r
    assert world.get("t72_far").alive               # 285 m away — survives


def test_request_fires_out_of_range_rejected():
    world = WorldState([
        Entity("m777_0", "m777_howitzer", "blue", "fires", [0, 0], ammo=5),
    ], _CAT)
    res = EffectResolver(world)
    # 2 km < min_range 4 km -> rejected, no ammo spent
    ev = res.submit(to_command("request_fires", {"asset_id": "m777_0", "target": [2000, 0]}), 1)
    assert ev.status == "rejected" and "out of range" in ev.detail
    assert world.get("m777_0").ammo == 5


def test_jam_suppresses_enemy_radar():
    world = WorldState([
        Entity("ew_0", "ew_jammer_gnd", "blue", "ew", [0, 0]),
        Entity("sa11", "sa11_sam", "red", "air_defense", [3000, 0], emitting=True),
    ], _CAT)
    res = EffectResolver(world)
    sig = SigintFeed(0.4)
    assert sig.observe(world, 1, np.random.default_rng(0)), "SIGINT sees the radiating SAM first"

    ev = res.submit(to_command("jam", {"asset_id": "ew_0", "target": [3000, 0]}), 1)
    assert ev.status == "suppressed" and "sa11" in ev.killed
    world.step(1.0)                                 # suppression takes hold
    assert world.get("sa11").emitting is False
    assert sig.observe(world, 2, np.random.default_rng(0)) == []   # radar dark -> SIGINT blind


def test_task_isr_confirms_hidden_track():
    """A hidden armor outside EO range; task the TB2 to its area; after the drone
    repositions, its feed picks the target up."""
    world = WorldState([
        Entity("tb2_0", "tb2_recon_uav", "blue", "isr", [0, 0]),
        Entity("hidden", "t72_tank", "red", "armor", [9000, 0]),
    ], _CAT)
    eo = PlatformSensorFeed("uav_eo", "eo_ir", 0.7)
    rng = np.random.default_rng(0)
    assert eo.observe(world, 0, rng) == []           # 9 km > 8 km EO range: unseen

    res = EffectResolver(world)
    res.submit(to_command("task_isr", {"asset_id": "tb2_0", "x": 9000, "y": 0}), 1)
    seen = False
    for tk in range(2, 40):
        world.step(1.0)
        if eo.observe(world, tk, rng):
            seen = True
            break
    assert seen, "after ISR reposition the EO feed should confirm the hidden armor"


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA AIP build #3 — Equipment effect resolution (AI operates real kit)")
    print(bar)

    print(f"\n[tools] auto-surfaced from the ActionType registry:")
    for t in ollama_tools():
        fn = t["function"]
        if fn["name"] in ("request_fires", "task_isr", "jam"):
            print(f"      {fn['name']:<14} req={fn['parameters']['required']}")

    print(f"\n[request_fires] M777 fires on a T-72 pair")
    world = WorldState([
        Entity("m777_0", "m777_howitzer", "blue", "fires", [-1000, 0], ammo=10),
        Entity("t72_0", "t72_tank", "red", "armor", [5000, 0]),
        Entity("t72_1", "t72_tank", "red", "armor", [5030, 0]),
        Entity("t72_far", "t72_tank", "red", "armor", [5300, 0]),
    ], _CAT)
    res = EffectResolver(world)
    ev = res.submit(to_command("request_fires", {"asset_id": "m777_0", "target": [5015, 0]}), 1)
    print(f"      t=1 {ev.status}: {ev.detail}")
    for tk in range(2, 60):
        world.step(1.0)
        for imp in res.step(tk):
            print(f"      t={tk} {imp.status}: {imp.detail}")
    alive = [e.entity_id for e in world.red if e.alive]
    print(f"      survivors: {alive}")

    print(f"\n[jam] EW jammer darkens an SA-11 radar")
    w2 = WorldState([
        Entity("ew_0", "ew_jammer_gnd", "blue", "ew", [0, 0]),
        Entity("sa11", "sa11_sam", "red", "air_defense", [3000, 0], emitting=True),
    ], _CAT)
    r2, sig = EffectResolver(w2), SigintFeed(0.4)
    print(f"      pre-jam  SIGINT sees: {[d.truth_id for d in sig.observe(w2, 1, np.random.default_rng(0))]}")
    ev = r2.submit(to_command("jam", {"asset_id": "ew_0", "target": [3000, 0]}), 1)
    w2.step(1.0)
    print(f"      {ev.status}: {ev.detail}")
    print(f"      post-jam SIGINT sees: {[d.truth_id for d in sig.observe(w2, 2, np.random.default_rng(0))]}")

    ok = (alive == ["t72_far"] and not w2.get("sa11").emitting)
    print("\n" + bar)
    print(f"EFFECT RESOLUTION {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"(fires neutralise in lethal_r; jam darkens radar; ISR confirms)")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
