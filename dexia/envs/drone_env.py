"""Gymnasium environment: single-drone 3-DOF wargame (Phase 1).

The env is a thin orchestrator over three *injected* components:

    * physics  : a ``PhysicsEngine``        (3-DOF integration)
    * comms    : a ``GilbertElliottChannel`` (link telemetry to base)
    * wind     : a ``WindField``             (domain randomization)

Keeping them injectable (constructor params, with sane defaults) is the key
design decision for scaling to MARL: a Ray RLlib ``MultiAgentEnv`` will hold one
physics engine + one comms channel per drone and reuse these exact classes. The
single-agent Gymnasium API here is the per-agent slice of that future env.

Task (Phase 1): fly from a random start to a goal waypoint while maintaining a
usable comms link back to a fixed base station. Reward shapes progress toward
the goal and penalises lost packets and control effort.
"""

from __future__ import annotations

from typing import Any

import numpy as np

try:  # Gymnasium is the target API; degrade gracefully if absent.
    import gymnasium as gym
    from gymnasium import spaces

    _GYM_BASE = gym.Env
except Exception:  # pragma: no cover
    gym = None
    spaces = None
    _GYM_BASE = object

from ..comms import ChannelState, GilbertElliottChannel
from ..domain_randomization import WindField
from ..physics import Kinematic3DOFEngine, PhysicsEngine


class DroneWargameEnv(_GYM_BASE):
    """Single-agent 3-DOF drone environment with bursty comms + wind."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        physics: PhysicsEngine | None = None,
        comms: GilbertElliottChannel | None = None,
        wind: WindField | None = None,
        base_station: np.ndarray | None = None,
        goal: np.ndarray | None = None,
        world_half_extent: float = 60.0,
        max_altitude: float = 50.0,
        goal_radius: float = 2.0,
        max_steps: int = 400,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self._rng = np.random.default_rng(seed)

        # Injected components (Phase-1 defaults if omitted) -> MARL-ready.
        self.physics = physics or Kinematic3DOFEngine(seed=seed)
        self.comms = comms or GilbertElliottChannel(seed=seed)
        self.wind = wind or WindField(seed=seed)

        self.base_station = (
            np.array([0.0, 0.0, 0.0]) if base_station is None
            else np.asarray(base_station, dtype=np.float64).reshape(3)
        )
        self._goal_fixed = goal is not None
        self.goal = (
            np.array([30.0, 25.0, 15.0]) if goal is None
            else np.asarray(goal, dtype=np.float64).reshape(3)
        )

        self.world_half_extent = float(world_half_extent)
        self.max_altitude = float(max_altitude)
        self.goal_radius = float(goal_radius)
        self.max_steps = int(max_steps)

        self._step_count = 0
        self._last_distance_to_goal = 0.0

        if spaces is not None:
            # action: per-axis normalised acceleration command in [-1, 1].
            self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(3,), dtype=np.float32)
            # observation: [rel_goal(3), vel(3), dist_base(1), rssi_norm(1),
            #               snr_norm(1), comms_state(1), last_packet_lost(1)]
            high = np.array(
                [np.inf] * 3 + [np.inf] * 3 + [np.inf, np.inf, np.inf, 1.0, 1.0],
                dtype=np.float32,
            )
            self.observation_space = spaces.Box(low=-high, high=high, dtype=np.float32)

    # ------------------------------------------------------------------ #
    def _distance(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(a - b))

    def _build_obs(self, channel_sample) -> np.ndarray:
        st = self.physics.get_state()
        rel_goal = self.goal - st.position
        dist_base = self._distance(st.position, self.base_station)
        obs = np.concatenate(
            [
                rel_goal,
                st.velocity,
                [dist_base],
                [channel_sample.rssi_dbm / 100.0],   # rough normalisation
                [channel_sample.snr_db / 100.0],
                [float(int(channel_sample.state))],
                [float(channel_sample.packet_lost)],
            ]
        ).astype(np.float32)
        return obs

    # ------------------------------------------------------------------ #
    def reset(
        self, *, seed: int | None = None, options: dict | None = None
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if seed is not None:
            self._rng = np.random.default_rng(seed)

        self._step_count = 0

        start = self._rng.uniform(
            low=[-self.world_half_extent * 0.5, -self.world_half_extent * 0.5, 5.0],
            high=[self.world_half_extent * 0.5, self.world_half_extent * 0.5, self.max_altitude * 0.5],
        )
        self.physics.reset(position=start, velocity=np.zeros(3))
        self.comms.reset(ChannelState.GOOD)
        self.wind.reset()

        if not self._goal_fixed:
            self.goal = self._rng.uniform(
                low=[-self.world_half_extent * 0.5, -self.world_half_extent * 0.5, 5.0],
                high=[self.world_half_extent * 0.5, self.world_half_extent * 0.5, self.max_altitude],
            )

        st = self.physics.get_state()
        self._last_distance_to_goal = self._distance(st.position, self.goal)

        dist_base = self._distance(st.position, self.base_station)
        sample = self.comms.step(dist_base)

        obs = self._build_obs(sample)
        info = self._make_info(sample, wind_force=np.zeros(3), reward=0.0, terminated=False)
        return obs, info

    # ------------------------------------------------------------------ #
    def step(
        self, action: np.ndarray
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        self._step_count += 1

        # 1) Domain randomization: wind force for this step.
        wind_force = self.wind.step()

        # 2) Physics: integrate 3-DOF state with wind disturbance.
        st = self.physics.step(action, external_force=wind_force)

        # 3) Comms: update Gilbert-Elliott link to base.
        dist_base = self._distance(st.position, self.base_station)
        sample = self.comms.step(dist_base)

        # 4) Reward shaping.
        dist_goal = self._distance(st.position, self.goal)
        progress = self._last_distance_to_goal - dist_goal
        self._last_distance_to_goal = dist_goal

        reward = 2.0 * progress
        reward -= 0.01 * float(np.sum(np.square(action)))   # control effort
        reward -= 0.05 * float(sample.packet_lost)          # link-loss penalty

        terminated = bool(dist_goal <= self.goal_radius)
        if terminated:
            reward += 100.0

        # Out-of-bounds termination.
        out_of_bounds = (
            np.any(np.abs(st.position[:2]) > self.world_half_extent)
            or st.position[2] < 0.0
            or st.position[2] > self.max_altitude
        )
        if out_of_bounds:
            terminated = True
            reward -= 50.0

        truncated = bool(self._step_count >= self.max_steps)

        obs = self._build_obs(sample)
        info = self._make_info(sample, wind_force, reward, terminated)
        return obs, float(reward), terminated, truncated, info

    # ------------------------------------------------------------------ #
    def _make_info(self, sample, wind_force, reward, terminated) -> dict[str, Any]:
        st = self.physics.get_state()
        return {
            "step": self._step_count,
            "position": st.position.copy(),
            "velocity": st.velocity.copy(),
            "goal": self.goal.copy(),
            "base_station": self.base_station.copy(),
            "distance_to_goal": self._distance(st.position, self.goal),
            "wind_force": np.asarray(wind_force, dtype=np.float64).copy(),
            "active_gusts": self.wind.active_gust_count,
            "reward": float(reward),
            "reached_goal": bool(terminated and self._last_distance_to_goal <= self.goal_radius),
            "comms": sample.as_dict(),
        }
