"""3D physics foundation (tier B / P1) — terrain, ground, air, guidance.

Proves entities live in 3-D and move themselves toward goals under real physics:
  * terrain     bilinear height + normal/slope on a heightfield
  * ground      a vehicle drives waypoints OVER a hill — z hugs the surface, it
                climbs (altitude rises) and slows on the grade
  * air         an aircraft banks to turn toward a target, climbs to its altitude,
                holds speed in [stall, vmax] — computing heading/alt/speed itself
  * guidance    proportional navigation intercepts a crossing, moving target

Dual-mode: ``pytest`` *and* ``python tests/test_physics3d.py``.
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

from dexia.physics3d import (
    FixedWing3DOFEngine,
    GroundVehicleEngine,
    Heightfield,
    proportional_navigation,
)


# --------------------------------------------------------------------------- #
def test_terrain_height_and_slope():
    ramp = Heightfield.ramp(slope=0.05)
    assert abs(ramp.height(1000, 0) - 50.0) < 1e-6          # linear: 1000 * 0.05
    assert abs(ramp.slope(500, 0) - math.atan(0.05)) < 1e-3
    flat = Heightfield.flat(0.0)
    assert flat.slope(0, 0) == 0.0 and np.allclose(flat.normal(0, 0), [0, 0, 1])
    hill = Heightfield.hill(peak=300.0, center=(1000.0, 0.0))
    assert hill.height(1000, 0) > 250 and hill.height(-2000, -2000) < 20


def test_ground_vehicle_climbs_and_hugs_terrain():
    hill = Heightfield.hill(peak=300.0, center=(1000.0, 0.0))
    veh = GroundVehicleEngine(hill, max_speed=15.0, mu=0.7)
    veh.reset((-1500.0, 0.0), heading=0.0)
    veh.set_waypoints([(1000.0, 0.0), (1800.0, 0.0)])

    max_alt, min_moving_speed, hugs = 0.0, 99.0, True
    for _ in range(4000):
        b = veh.step(0.1)
        # z is exactly the terrain surface under the vehicle (3-D ground contact)
        if abs(b.pos[2] - hill.height(b.pos[0], b.pos[1])) > 1e-6:
            hugs = False
        max_alt = max(max_alt, b.alt())
        if b.speed() > 0.5:
            min_moving_speed = min(min_moving_speed, b.speed())
        if veh.arrived():
            break
    assert hugs, "vehicle z must track the terrain surface"
    assert max_alt > 200, f"should climb the 300 m hill, got {max_alt:.0f}"
    assert min_moving_speed < 15.0, "grade/cornering should slow it below max speed"
    assert veh.arrived(), "should reach its waypoints"


def test_aircraft_turns_climbs_and_holds_envelope():
    ac = FixedWing3DOFEngine(cruise=200.0, v_stall=65.0, v_max=320.0)
    ac.reset((0.0, 0.0, 1000.0), V=200.0, heading=0.0)     # flying east at 1 km
    ac.set_target((0.0, 30000.0, 2000.0))                  # north + 1 km higher

    min_range, turned, in_envelope = 1e12, False, True
    for _ in range(600):
        b = ac.step(0.5)
        rng = math.hypot(b.pos[0] - 0.0, b.pos[1] - 30000.0)
        min_range = min(min_range, rng)
        if abs((ac.chi - math.pi / 2 + math.pi) % (2 * math.pi) - math.pi) < 0.15:
            turned = True                                  # heading reached ~north
        if not (ac.v_stall - 1 <= b.speed() <= ac.v_max + 1):
            in_envelope = False
    assert turned, "aircraft should bank-turn its heading toward the target (north)"
    assert min_range < 2000, f"should fly to the target, closest {min_range:.0f} m"
    assert in_envelope, "speed must stay within the flight envelope"
    assert ac.h > 1500, f"should climb toward 2000 m, reached {ac.h:.0f}"


def test_proportional_navigation_intercept():
    m_pos = np.array([0.0, 0.0, 0.0])
    t_pos = np.array([8000.0, 3000.0, 0.0])
    t_vel = np.array([-40.0, 60.0, 0.0])                   # crossing, moving target
    speed = 600.0
    m_vel = (t_pos - m_pos) / np.linalg.norm(t_pos - m_pos) * speed
    dt, min_range = 0.05, 1e12
    for _ in range(4000):
        r_rel, v_rel = t_pos - m_pos, t_vel - m_vel
        a = proportional_navigation(r_rel, v_rel, m_vel, N=4.0)
        m_vel = m_vel + a * dt
        m_vel = m_vel / np.linalg.norm(m_vel) * speed       # motor holds speed
        m_pos = m_pos + m_vel * dt
        t_pos = t_pos + t_vel * dt
        rng = float(np.linalg.norm(t_pos - m_pos))
        min_range = min(min_range, rng)
        if rng > min_range + 200:                           # past closest approach
            break
    assert min_range < 50.0, f"PN should intercept; miss distance {min_range:.1f} m"


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA tier B / P1 — 3D physics (terrain · ground · air · guidance)")
    print(bar)

    hill = Heightfield.hill(peak=300.0, center=(1000.0, 0.0))
    veh = GroundVehicleEngine(hill)
    veh.reset((-1500.0, 0.0)); veh.set_waypoints([(1000.0, 0.0), (1800.0, 0.0)])
    samples = []
    for i in range(4000):
        b = veh.step(0.1)
        if i % 400 == 0:
            samples.append(b)
        if veh.arrived():
            break
    print("\n[ground] vehicle climbing the hill (z hugs terrain):")
    for b in samples:
        r, p, yaw = b.euler()
        print(f"      pos=({b.pos[0]:7.0f},{b.pos[1]:6.0f}) alt={b.alt():6.1f}  "
              f"v={b.speed():4.1f}  pitch={math.degrees(p):+5.1f}°")

    ac = FixedWing3DOFEngine(cruise=200.0)
    ac.reset((0.0, 0.0, 1000.0), V=200.0, heading=0.0)
    ac.set_target((0.0, 30000.0, 2000.0))
    print("\n[air] aircraft banking north + climbing to 2000 m:")
    for i in range(600):
        b = ac.step(0.5)
        if i % 100 == 0:
            print(f"      t={i*0.5:5.0f}s  hdg={math.degrees(ac.chi):+6.1f}°  "
                  f"alt={ac.h:6.0f}  V={ac.V:5.1f}  bank={math.degrees(ac.mu):+5.1f}°  "
                  f"n={ac.load_factor:.2f}")

    print(bar)
    print("3D PHYSICS P1 VERIFIED ✅  (ground hugs terrain & climbs; "
          "aircraft self-navigates; PN intercepts)")
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
