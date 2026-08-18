"""Abstract physics-engine interface.

The environment talks to the physics layer *only* through this interface.
That decoupling is what lets us swap the lightweight NumPy 3-DOF integrator
used in Phase 1 for MuJoCo or Isaac Gym in a later phase without touching the
Gymnasium env or the MARL wiring.

Phase 1 constraint: state is **3-DOF translation only** (x, y, z position +
velocity). No attitude / rotation. 6-DOF will be unlocked later by extending
``DroneState`` and adding a new engine implementation behind this same ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class DroneState:
    """3-DOF kinematic state of a single drone.

    Attributes
    ----------
    position : np.ndarray  shape (3,)   metres, world frame [x, y, z]
    velocity : np.ndarray  shape (3,)   m/s, world frame
    """

    position: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))

    def copy(self) -> "DroneState":
        return DroneState(self.position.copy(), self.velocity.copy())

    def as_vector(self) -> np.ndarray:
        """Flatten to a 6-vector [px, py, pz, vx, vy, vz]."""
        return np.concatenate([self.position, self.velocity])


@dataclass
class DroneState6DOF(DroneState):
    """Full 6-DOF rigid-body state of a single drone (Phase 2+).

    Extends :class:`DroneState` (so it is API-compatible everywhere a
    ``DroneState`` is expected) with attitude and angular rates.

    Attributes
    ----------
    orientation : np.ndarray shape (3,)
        Euler angles [roll, pitch, yaw] in radians (world/ZYX convention).
    angular_velocity : np.ndarray shape (3,)
        Body angular velocity [wx, wy, wz] in rad/s.
    quaternion : np.ndarray shape (4,)
        Attitude as a unit quaternion [w, x, y, z] (kept for lossless state).
    """

    orientation: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    angular_velocity: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    quaternion: np.ndarray = field(
        default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    )

    def copy(self) -> "DroneState6DOF":
        return DroneState6DOF(
            self.position.copy(),
            self.velocity.copy(),
            self.orientation.copy(),
            self.angular_velocity.copy(),
            self.quaternion.copy(),
        )

    def as_vector(self) -> np.ndarray:
        """Flatten to a 12-vector [pos(3), vel(3), euler(3), omega(3)]."""
        return np.concatenate(
            [self.position, self.velocity, self.orientation, self.angular_velocity]
        )


class PhysicsEngine(ABC):
    """Minimal physics-engine contract.

    Implementations integrate one drone's 3-DOF state forward in time given a
    control action and an optional external force (e.g. wind). Designed to be
    vectorizable per-agent so a MARL runner can hold one engine per drone or a
    batched engine later.
    """

    #: simulation timestep in seconds
    dt: float

    @abstractmethod
    def reset(self, position: np.ndarray, velocity: np.ndarray | None = None) -> DroneState:
        """Reset the engine to an initial 3-DOF state and return it."""

    @abstractmethod
    def step(self, action: np.ndarray, external_force: np.ndarray | None = None) -> DroneState:
        """Advance the simulation by one timestep.

        Parameters
        ----------
        action : np.ndarray shape (3,)
            Commanded control in the x, y, z translational axes (acceleration
            command, normalised to [-1, 1] by the env).
        external_force : np.ndarray shape (3,), optional
            Disturbance force in Newtons (e.g. wind gust). Defaults to zero.

        Returns
        -------
        DroneState : the new 3-DOF state after integration.
        """

    @abstractmethod
    def get_state(self) -> DroneState:
        """Return a copy of the current 3-DOF state."""
