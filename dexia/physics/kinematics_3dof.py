"""Lightweight 3-DOF point-mass kinematic integrator.

This is the Phase-1 physics backend. It models a drone as a point mass with
translational degrees of freedom only (x, y, z). Integration uses semi-implicit
(symplectic) Euler, which is stable for the small timesteps used here.

Force model::

    F_total = F_thrust + F_gravity + F_drag + F_external(wind)
    a       = F_total / m
    v      <- v + a * dt          (velocity updated first -> semi-implicit)
    x      <- x + v * dt

The engine is deliberately framework-free (pure NumPy) so it has zero GPU /
driver requirements and runs deterministically in CI. It satisfies the same
``PhysicsEngine`` ABC that a future MuJoCo / Isaac Gym backend will implement.
"""

from __future__ import annotations

import numpy as np

from .base import DroneState, PhysicsEngine


class Kinematic3DOFEngine(PhysicsEngine):
    def __init__(
        self,
        dt: float = 0.05,
        mass: float = 1.5,
        max_thrust: float = 12.0,
        drag_coeff: float = 0.25,
        gravity: float = 9.81,
        gravity_compensation: bool = True,
        max_speed: float = 20.0,
        seed: int | None = None,
    ) -> None:
        """
        Parameters
        ----------
        dt : timestep [s]
        mass : drone mass [kg]
        max_thrust : peak force per axis the controller can command [N]
        drag_coeff : linear drag coefficient (F_drag = -c * v) [N*s/m]
        gravity : gravitational acceleration magnitude [m/s^2]
        gravity_compensation : if True the controller auto-hovers (gravity is
            internally cancelled) so the agent only learns translational
            corrections. Keeps Phase-1 learning tractable.
        max_speed : velocity magnitude clamp [m/s]
        """
        self.dt = float(dt)
        self.mass = float(mass)
        self.max_thrust = float(max_thrust)
        self.drag_coeff = float(drag_coeff)
        self.gravity = float(gravity)
        self.gravity_compensation = bool(gravity_compensation)
        self.max_speed = float(max_speed)
        self._rng = np.random.default_rng(seed)

        self._state = DroneState()

    # ------------------------------------------------------------------ #
    # PhysicsEngine interface
    # ------------------------------------------------------------------ #
    def reset(self, position: np.ndarray, velocity: np.ndarray | None = None) -> DroneState:
        pos = np.asarray(position, dtype=np.float64).reshape(3)
        vel = (
            np.zeros(3, dtype=np.float64)
            if velocity is None
            else np.asarray(velocity, dtype=np.float64).reshape(3)
        )
        self._state = DroneState(pos.copy(), vel.copy())
        return self._state.copy()

    def step(self, action: np.ndarray, external_force: np.ndarray | None = None) -> DroneState:
        action = np.clip(np.asarray(action, dtype=np.float64).reshape(3), -1.0, 1.0)
        ext = (
            np.zeros(3, dtype=np.float64)
            if external_force is None
            else np.asarray(external_force, dtype=np.float64).reshape(3)
        )

        # Commanded thrust force (per-axis), normalised action -> Newtons.
        f_thrust = action * self.max_thrust

        # Gravity acts on -z; optionally compensated (auto-hover).
        f_gravity = np.array([0.0, 0.0, -self.gravity * self.mass], dtype=np.float64)
        if self.gravity_compensation:
            f_gravity[:] = 0.0

        # Linear aerodynamic drag opposes velocity.
        f_drag = -self.drag_coeff * self._state.velocity

        f_total = f_thrust + f_gravity + f_drag + ext
        accel = f_total / self.mass

        # Semi-implicit Euler.
        new_vel = self._state.velocity + accel * self.dt
        speed = float(np.linalg.norm(new_vel))
        if speed > self.max_speed:
            new_vel *= self.max_speed / speed
        new_pos = self._state.position + new_vel * self.dt

        self._state = DroneState(new_pos, new_vel)
        return self._state.copy()

    def get_state(self) -> DroneState:
        return self._state.copy()
