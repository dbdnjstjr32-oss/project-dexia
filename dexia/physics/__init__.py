from .base import PhysicsEngine, DroneState, DroneState6DOF
from .kinematics_3dof import Kinematic3DOFEngine

# MuJoCo backend is optional (heavy dep); import lazily so Phase-1-only
# environments without mujoco installed still work.
try:
    from .mujoco_engine import MuJoCoQuadEngine
    from .mujoco_builder import (
        DEFAULT_PROFILE,
        generate_mjcf,
        normalize_profile,
        profile_summary,
    )

    _HAS_MUJOCO = True
except Exception:  # pragma: no cover
    MuJoCoQuadEngine = None  # type: ignore
    generate_mjcf = None  # type: ignore
    normalize_profile = None  # type: ignore
    profile_summary = None  # type: ignore
    DEFAULT_PROFILE = None  # type: ignore
    _HAS_MUJOCO = False

__all__ = [
    "PhysicsEngine",
    "DroneState",
    "DroneState6DOF",
    "Kinematic3DOFEngine",
    "MuJoCoQuadEngine",
    "generate_mjcf",
    "normalize_profile",
    "profile_summary",
    "DEFAULT_PROFILE",
]
