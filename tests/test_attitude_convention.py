"""Attitude convention regression (audit team1-H1).

The live MuJoCo spawn Euler->quat must match the canonical Body6 ZYX convention
(``physics3d.state.quat_from_euler``) so the two engines' attitudes are
comparable. Identity spawn already agreed; this guards non-zero attitudes too
(the old ``"xyz"`` sequence diverged silently off-level).
"""

from __future__ import annotations

import numpy as np

from dexia.physics.mujoco_engine import MuJoCoQuadEngine
from dexia.physics3d.state import quat_from_euler


def test_mujoco_spawn_quat_matches_zyx_convention():
    eng = MuJoCoQuadEngine()                      # default standard quad
    rpy = [0.3, 0.2, 0.1]                         # roll, pitch, yaw [rad]
    eng.reset(position=[0.0, 0.0, 5.0], orientation=rpy)
    q = np.array(eng.data.qpos[eng._qpos_adr + 3 : eng._qpos_adr + 7])
    ref = quat_from_euler(*rpy)                   # canonical ZYX [w,x,y,z]
    assert np.allclose(q, ref, atol=1e-6), f"MuJoCo spawn quat {q} != canonical ZYX {ref}"


def test_identity_spawn_is_level():
    eng = MuJoCoQuadEngine()
    eng.reset(position=[0.0, 0.0, 5.0])           # no orientation -> level
    q = np.array(eng.data.qpos[eng._qpos_adr + 3 : eng._qpos_adr + 7])
    assert np.allclose(q, [1.0, 0.0, 0.0, 0.0], atol=1e-6)
