"""Audit C-1: the ISR detection gate must not collapse with altitude.

The old ``_is_detection`` used a single 3-D sphere to the ground target *plus* a
minimum-altitude floor, so the two constraints fought each other: a recon
climbing above ~``detection_radius`` of altitude could never satisfy the sphere,
so the broadcast — and therefore the whole kill chain — was unwinnable from
altitude. Detection now uses a horizontal sensor footprint + a min-altitude LOS
floor, which is both winnable and more physical for a downward-looking sensor.
"""
from __future__ import annotations

import numpy as np
import pytest

from dexia.envs.drone_marl_env import DroneMARLEnv


class _St:
    """Minimal stand-in for an engine state (only .position is read)."""
    def __init__(self, pos):
        self.position = np.asarray(pos, dtype=np.float64)


@pytest.fixture(scope="module")
def env():
    e = DroneMARLEnv({"num_recon": 1, "num_kami": 1, "seed": 5})
    e.reset(seed=5)
    return e


def test_detection_holds_at_any_altitude_over_target(env):
    tx, ty, _ = env.target
    floor = env.los_min_altitude
    # directly over the target, detection holds no matter how high we climb
    for alt in (floor, floor + 2.0, 8.0, 15.0, 30.0):
        assert env._is_detection(_St([tx, ty, alt])), f"should detect at alt={alt}"


def test_detection_respects_horizontal_footprint_and_floor(env):
    tx, ty, _ = env.target
    # outside the horizontal footprint -> no detection even at good altitude
    assert not env._is_detection(_St([tx + env.detection_radius + 1.0, ty, 8.0]))
    # below the LOS floor -> no detection even directly over the target
    assert not env._is_detection(_St([tx, ty, env.los_min_altitude - 0.5]))


def test_old_3d_sphere_would_have_failed_high(env):
    # The exact case the old single-sphere model made geometrically impossible.
    tx, ty, tz = env.target
    high = _St([tx, ty, tz + env.detection_radius + 3.0])  # far above in 3-D
    old_3d_ok = float(np.linalg.norm(high.position - env.target)) <= env.detection_radius
    assert not old_3d_ok            # old model: impossible from this altitude
    assert env._is_detection(high)  # new model: detects (winnable)
