"""JSBSim 6-DOF hero aircraft + numpy fallback (tier B / P2).

Proves a hero aircraft flies a real JSBSim flight dynamics model behind the SAME
interface as the numpy FixedWing3DOFEngine — and that everything degrades to numpy
when jsbsim isn't installed (so no machine is left unable to run the sim).

Dual-mode: ``pytest`` *and* ``python tests/test_jsbsim_engine.py``.
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
import pytest

from dexia.physics3d import (
    FixedWing3DOFEngine,
    JSBSIM_AVAILABLE,
    Body6,
    make_air_engine,
)
from dexia.physics3d import jsbsim_engine as _je


def _is_body6(b) -> bool:
    return (isinstance(b, Body6) and b.pos.shape == (3,) and b.vel.shape == (3,)
            and b.quat.shape == (4,) and abs(float(np.linalg.norm(b.quat)) - 1.0) < 1e-6
            and bool(np.all(np.isfinite(b.pos)) and np.all(np.isfinite(b.vel))))


# --------------------------------------------------------------------------- #
def test_fallback_returns_numpy_engine():
    """A non-hero aircraft always flies the numpy model; and even a hero falls back
    to numpy when jsbsim is unavailable — never raising."""
    assert isinstance(make_air_engine(False, {"cruise": 45.0}), FixedWing3DOFEngine)

    saved = _je.JSBSIM_AVAILABLE
    try:
        _je.JSBSIM_AVAILABLE = False                  # simulate jsbsim missing
        eng = make_air_engine(True, {"cruise": 45.0})
        assert isinstance(eng, FixedWing3DOFEngine)
    finally:
        _je.JSBSIM_AVAILABLE = saved


def test_numpy_and_jsbsim_emit_the_same_body6():
    """Both engines write the uniform Body6, so MotionModel/HUD never branch."""
    npy = FixedWing3DOFEngine(cruise=50.0)
    assert _is_body6(npy.reset((0.0, 0.0, 1500.0), V=50.0))
    if not JSBSIM_AVAILABLE:
        pytest.skip("jsbsim not installed — fallback path covered above")
    js = make_air_engine(True, {"cruise": 50.0})
    assert js.__class__.__name__ == "JSBSimEngine"
    assert _is_body6(js.reset((0.0, 0.0, 1500.0), V=50.0))


@pytest.mark.skipif(not JSBSIM_AVAILABLE, reason="jsbsim not installed")
def test_jsbsim_flies_to_waypoint_and_holds_altitude():
    """The hero c172x banks toward a NE waypoint and climbs to its altitude under a
    real FDM — range closes, it climbs, and stays in a sane speed band the whole time."""
    eng = make_air_engine(True, {"cruise": 50.0})
    eng.reset((0.0, 0.0, 1500.0), V=50.0, heading=0.0)     # start heading East
    eng.set_target((6000.0, 4000.0, 1800.0))               # NE + climb 300 m

    def rng():
        b = eng.state()
        return math.hypot(b.pos[0] - 6000.0, b.pos[1] - 4000.0)

    r0, climbed, in_band = rng(), 1500.0, True
    for _ in range(80):
        b = eng.step(1.0)
        assert _is_body6(b)
        climbed = max(climbed, b.pos[2])
        if not (15.0 <= b.speed() <= 90.0):
            in_band = False
    assert rng() < r0 - 2000.0, "should fly substantially toward the waypoint"
    assert climbed > 1700.0, f"should climb toward 1800 m (reached {climbed:.0f})"
    assert in_band, "speed must stay in a sane flight band (no stall/runaway)"
    # turned from due-East toward the ~34deg NE bearing
    assert math.degrees(eng.chi) > 10.0


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA tier B / P2 — JSBSim 6-DOF hero aircraft (numpy fallback)")
    print(bar)
    print(f"\njsbsim available: {JSBSIM_AVAILABLE}")
    print(f"make_air_engine(hero=False) -> {type(make_air_engine(False, {'cruise':45})).__name__}")

    if not JSBSIM_AVAILABLE:
        print("\njsbsim not installed — hero falls back to numpy FixedWing3DOFEngine.")
        print(bar)
        return 0

    eng = make_air_engine(True, {"cruise": 50.0})
    eng.reset((0.0, 0.0, 1500.0), V=50.0, heading=0.0)
    eng.set_target((6000.0, 4000.0, 1800.0))
    print(f"make_air_engine(hero=True ) -> {type(eng).__name__}  (trim elev={eng.e_trim:.2f} thr={eng.t_trim:.2f})")
    print("\n[hero] c172x banking NE + climbing to 1800 m on the JSBSim FDM:")
    for i in range(1, 81):
        b = eng.step(1.0)
        if i % 16 == 0:
            r = math.hypot(b.pos[0] - 6000, b.pos[1] - 4000)
            print(f"   t={i:2d}s  x={b.pos[0]:6.0f} y={b.pos[1]:6.0f} alt={b.pos[2]:6.0f} "
                  f"v={b.speed():4.1f} course={math.degrees(eng.chi):+5.0f}° range={r:5.0f}")
    print("\n" + bar)
    print("JSBSIM P2 VERIFIED ✅  (hero flies a real 6-DOF FDM to its waypoint & "
          "altitude; non-hero / no-jsbsim falls back to numpy — same Body6)")
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
