"""dexia.physics3d — 3D physics-based motion models (tier B foundation).

Every ground and air entity integrates a real 3-DOF+ state in world ENU space:
ground vehicles hug the terrain (bicycle model + heightfield), aircraft compute
their own heading/altitude/speed from a point-mass aerodynamic model, and both
pursue goals through guidance laws. All write a uniform ``Body6`` state, and all
sit behind a swappable engine boundary (cf. physics/base.py) so a JSBSim 6-DOF
fixed-wing engine (P2) drops in for hero aircraft without changing callers.

See DESIGN_AIP.md / the physics design for the equations.
"""

from __future__ import annotations

from .air import FixedWing3DOFEngine, isa_density
from .ballistic import BallisticEngine
from .geometry import clear_los, distance3d
from .ground import GroundVehicleEngine
from .guidance import arrive, proportional_navigation, pure_pursuit, seek
from .integrate import clamp, rk4, semi_implicit, wrap_angle
from .missile import MissileEngine
from .state import Body6, quat_from_euler, quat_integrate, quat_to_euler
from .terrain import Heightfield

__all__ = [
    "Body6", "quat_from_euler", "quat_to_euler", "quat_integrate",
    "Heightfield",
    "GroundVehicleEngine", "FixedWing3DOFEngine", "isa_density",
    "BallisticEngine", "MissileEngine",
    "distance3d", "clear_los",
    "pure_pursuit", "seek", "arrive", "proportional_navigation",
    "clamp", "wrap_angle", "semi_implicit", "rk4",
]
