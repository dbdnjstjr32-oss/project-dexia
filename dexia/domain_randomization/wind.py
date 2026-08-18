"""Domain randomization: wind disturbance field.

Phase 1 injects *weak* wind to perturb the 3-DOF state. Two components:

1. A persistent ambient breeze modelled as an Ornstein-Uhlenbeck (OU) process,
   which gives temporally-correlated (not white) low-amplitude wind force.
2. Discrete *gusts* - short-lived force impulses with a smooth (raised-cosine)
   envelope. Gusts occur randomly, OR can be triggered on demand via
   :meth:`trigger_gust`, which the Phase-1 test uses to force a gust.

The field returns a force vector [N] added to the physics engine each step, so
this stays fully decoupled from both the physics and the env (swap it out or
add turbulence models later without touching anything else).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np


@dataclass
class WindGust:
    """A single active gust."""

    direction: np.ndarray          # unit vector (3,)
    peak_force: float              # N
    start_step: int
    duration_steps: int
    age: int = 0

    def force(self) -> np.ndarray:
        """Raised-cosine envelope: 0 -> peak -> 0 over the gust lifetime."""
        if self.duration_steps <= 0:
            return np.zeros(3)
        phase = np.pi * self.age / self.duration_steps  # 0..pi
        envelope = np.sin(phase) ** 2  # smooth 0->1->0
        return self.direction * (self.peak_force * envelope)

    @property
    def active(self) -> bool:
        return self.age < self.duration_steps


class WindField:
    def __init__(
        self,
        ambient_mean: np.ndarray | None = None,
        ambient_sigma: float = 0.15,
        ambient_theta: float = 0.15,
        gust_probability: float = 0.0,
        gust_peak_range: tuple[float, float] = (1.0, 3.0),
        gust_duration_range: tuple[int, int] = (8, 20),
        max_ambient_force: float = 1.0,
        dt: float = 0.02,
        seed: int | None = None,
    ) -> None:
        """
        Parameters
        ----------
        ambient_mean : (3,) mean ambient wind force [N]. Default light +x breeze.
        ambient_sigma : OU diffusion coefficient (gustiness of the ambient breeze).
        ambient_theta : OU mean-reversion rate [1/s].
        gust_probability : per-step probability a random gust spawns. Set to 0
            for a deterministic test where gusts are only triggered manually.
        gust_peak_range : (min, max) peak gust force [N] - kept "weak".
        gust_duration_range : (min, max) gust duration in steps.
        max_ambient_force : clamp on ambient OU force magnitude [N].
        dt : simulation timestep [s]. Used in the OU discretisation so that the
            OU statistics are Hz-independent (default 0.02 s = 50 Hz).
        """
        self.ambient_mean = (
            np.array([0.4, 0.0, 0.0], dtype=np.float64)
            if ambient_mean is None
            else np.asarray(ambient_mean, dtype=np.float64).reshape(3)
        )
        self.ambient_sigma = float(ambient_sigma)
        self.ambient_theta = float(ambient_theta)
        self.gust_probability = float(gust_probability)
        self.gust_peak_range = gust_peak_range
        self.gust_duration_range = gust_duration_range
        self.max_ambient_force = float(max_ambient_force)
        self.dt = float(dt)   # simulation step size [s] — fixes OU Hz-dependency
        self._rng = np.random.default_rng(seed)

        self._ambient = self.ambient_mean.copy()
        self._gusts: List[WindGust] = []
        self._step = 0

    # ------------------------------------------------------------------ #
    def reset(self, seed: int | None = None) -> None:
        """Reset the wind field to its initial state.

        [결함 22] seed 가 주어지면 내부 RNG 를 재초기화하여 에피소드 간 재현성을
        보장한다. seed=None 이면 새로운 비결정적 RNG 를 생성한다.
        """
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        else:
            self._rng = np.random.default_rng()
        self._ambient = self.ambient_mean.copy()
        self._gusts.clear()
        self._step = 0

    def trigger_gust(
        self,
        direction: np.ndarray | None = None,
        peak_force: float | None = None,
        duration_steps: int | None = None,
    ) -> WindGust:
        """Manually inject a gust (used by the Phase-1 test for determinism)."""
        if direction is None:
            d = self._rng.normal(size=3)
        else:
            d = np.asarray(direction, dtype=np.float64).reshape(3)
        norm = np.linalg.norm(d)
        d = d / norm if norm > 1e-9 else np.array([1.0, 0.0, 0.0])

        if peak_force is None:
            peak_force = float(self._rng.uniform(*self.gust_peak_range))
        if duration_steps is None:
            duration_steps = int(self._rng.integers(*self.gust_duration_range))

        gust = WindGust(
            direction=d,
            peak_force=float(peak_force),
            start_step=self._step,
            duration_steps=int(duration_steps),
        )
        self._gusts.append(gust)
        return gust

    def _step_ambient(self) -> None:
        """One OU update of the ambient breeze.

        Exact Euler-Maruyama discretisation of the Ornstein-Uhlenbeck SDE:
            dx = theta * (mu - x) * dt + sigma * sqrt(dt) * N(0, 1)

        Both the drift and diffusion terms are scaled by dt (or sqrt(dt)) so
        the OU statistics are independent of the simulation Hz.
        """
        dt = self.dt
        drift = self.ambient_theta * (self.ambient_mean - self._ambient) * dt
        diffusion = self.ambient_sigma * np.sqrt(dt) * self._rng.normal(size=3)
        self._ambient = self._ambient + drift + diffusion
        mag = np.linalg.norm(self._ambient)
        if mag > self.max_ambient_force:
            self._ambient *= self.max_ambient_force / mag

    def step(self) -> np.ndarray:
        """Advance the wind field and return the total force vector [N]."""
        self._step_ambient()

        # Spontaneous random gusts.
        if self._rng.random() < self.gust_probability:
            self.trigger_gust()

        gust_force = np.zeros(3, dtype=np.float64)
        for gust in self._gusts:
            gust_force += gust.force()
            gust.age += 1
        self._gusts = [g for g in self._gusts if g.active]

        self._step += 1
        return self._ambient + gust_force

    @property
    def active_gust_count(self) -> int:
        return len(self._gusts)
