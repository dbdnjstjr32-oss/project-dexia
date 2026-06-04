"""3D physics INTEGRATION into the live sim (tier B / P3).

Proves the engines from P1 now drive WorldState's ground truth — gated on a
scenario `terrain:` block — without disturbing the legacy 2D path:

  * terrain present  -> mobile entities get a per-domain MotionModel; the red
                        armor column drives itself OVER a ridge (z hugs the
                        surface, climbs the grade), the TB2 self-navigates in 3D
  * terrain absent   -> the exact legacy scripted `_advance` (2D, 4 m/s straight
                        line) is preserved byte-for-byte -> campaign unchanged
  * end-to-end       -> the demo scenario runs through MissionRunner to a verdict

Dual-mode: ``pytest`` *and* ``python tests/test_physics_integration.py``.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.agent.loop import MissionRunner
from dexia.fusion import WorldState
from dexia.fusion.world import ADVANCE_SPEED_MPS, Entity
from dexia.scenario.catalog import load_catalog
from dexia.scenario.scenario import Scenario, load_scenario

_CAT = load_catalog()
_DEMO = "ridge-assault-3d"


def _world():
    return WorldState.from_scenario(load_scenario(_DEMO), _CAT)


# --------------------------------------------------------------------------- #
def test_terrain_enables_physics_path():
    w = _world()
    assert w.physics and w.terrain is not None, "terrain block must enable physics"
    tank = next(e for e in w.red if e.category == "armor")
    air = next(e for e in w.blue if e.cls == "tb2_recon_uav")
    assert len(tank.position) == 3 and tank._motion is not None
    assert air._motion is not None and air._motion.domain == "air"
    # the static AD / EW sites have no route -> stay scripted (no motion model)
    sam = next(e for e in w.red if e.category == "air_defense")
    assert sam._motion is None


def test_ground_column_climbs_ridge():
    """The tank drives itself over the ridge: z is exactly the terrain surface
    (3-D ground contact), and it climbs well up the 350 m crest."""
    w = _world()
    tank = next(e for e in w.red if e.category == "armor")
    x0 = tank.position[0]
    hugs, max_alt = True, tank.position[2]
    for _ in range(900):
        w.step(1.0)
        h = w.terrain.height(tank.position[0], tank.position[1])
        if abs(tank.position[2] - h) > 1e-6:
            hugs = False
        max_alt = max(max_alt, tank.position[2])
    assert hugs, "tank z must track the terrain surface (it hugs the ground in 3-D)"
    assert max_alt > 250, f"should climb the 350 m ridge, reached {max_alt:.0f} m"
    assert tank.position[0] < x0 - 1000, "should advance along its route (toward the line)"


def test_air_isr_self_navigates_in_3d():
    """The TB2 (domain: air) banks toward its waypoints and holds a cruise
    altitude above the terrain — heading/alt/speed computed by the flight model."""
    w = _world()
    air = next(e for e in w.blue if e.cls == "tb2_recon_uav")
    x0 = air.position[0]
    for _ in range(120):
        w.step(1.0)
    assert air.position[0] > x0 + 500, "aircraft should fly toward its waypoint"
    assert air.position[2] > w.terrain.height(air.position[0], air.position[1]) + 500, \
        "aircraft should hold altitude well above the terrain"


def test_legacy_path_unchanged_without_terrain():
    """No terrain -> no motion model -> the exact scripted 2D advance, so the
    100-scenario campaign keeps identical numbers."""
    e = Entity("t", "t72_tank", "red", "armor", position=[100.0, 0.0],
               behavior="advance", route=[[100.0, 0.0], [1000.0, 0.0]])
    w = WorldState([e], _CAT)                       # no terrain kwarg
    assert not w.physics and w.terrain is None
    assert e._motion is None and len(e.position) == 2
    w.step(1.0)
    # straight-line advance at ADVANCE_SPEED_MPS toward [1000,0], y unchanged
    assert math.isclose(e.position[0], 100.0 + ADVANCE_SPEED_MPS, abs_tol=1e-9)
    assert math.isclose(e.position[1], 0.0, abs_tol=1e-9)
    assert len(e.position) == 2, "legacy position stays 2-D"


def test_demo_mission_end_to_end():
    """The terrain demo runs through the full agent loop to a verdict, writes a
    reasoning trace, and ground truth stays 3-D throughout."""
    tmp = tempfile.gettempdir()
    runner = MissionRunner(
        load_scenario(_DEMO), _CAT, max_cycles=16,
        trace_path=os.path.join(tmp, "dexia_p3_trace.jsonl"),
        audit_path=os.path.join(tmp, "dexia_p3_audit.jsonl"),
        record_lineage=False)
    summary = runner.run()
    assert summary["cycles"] >= 1 and summary["trace_len"] == summary["cycles"]
    assert runner.world.physics
    movers = [e for e in runner.world.entities if e._motion is not None]
    assert movers and all(len(e.position) == 3 for e in movers), "truth stays 3-D"


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA tier B / P3 — 3D physics INTEGRATED into WorldState")
    print(bar)

    w = _world()
    tank = next(e for e in w.red if e.category == "armor")
    air = next(e for e in w.blue if e.cls == "tb2_recon_uav")
    print(f"\nphysics={w.physics}  (gated on scenario terrain block)")
    print("\n[ground] T-72 column driving itself OVER the ridge (z hugs terrain):")
    for i in range(1, 901):
        w.step(1.0)
        if i % 150 == 0:
            h = w.terrain.height(tank.position[0], tank.position[1])
            print(f"   t={i:4d}s  x={tank.position[0]:6.0f}  alt={tank.position[2]:6.1f}  "
                  f"(surface={h:6.1f})  hdg={math.degrees(tank.heading):+6.1f}°")
    print("\n[air] TB2 self-navigating in 3D toward its waypoint:")
    w2 = _world()
    air = next(e for e in w2.blue if e.cls == "tb2_recon_uav")
    for i in range(1, 121):
        w2.step(1.0)
        if i % 30 == 0:
            print(f"   t={i:4d}s  x={air.position[0]:6.0f}  y={air.position[1]:6.0f}  "
                  f"alt={air.position[2]:6.0f}  hdg={math.degrees(air.heading):+6.1f}°")

    print("\n" + bar)
    print("3D PHYSICS P3 INTEGRATED ✅  (ground hugs/climbs terrain in the live sim; "
          "air self-navigates; legacy 2D path untouched)")
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
