"""Phase-2 6-DOF quadcopter environment with a curriculum API ("Flight School").

A single-agent Gymnasium env wrapping the :class:`MuJoCoQuadEngine`. It exposes
a **multi-stage curriculum**:

    Stage 1  HOVER     : hold a fixed point with level attitude.
    Stage 2  WAYPOINT  : fly to a randomised target X/Y/Z and stabilise there.

Environmental stressors (wind, comms packet loss) from Phase 1 are intentionally
**disabled** here so the agent can focus on attitude control + navigation under
ideal conditions (per the Phase-2 brief).

Curriculum API
--------------
* ``env.set_stage(stage)`` / ``env.get_stage()``           - imperative control
* ``env.set_task(task)`` / ``env.get_task()``              - Ray RLlib
  ``TaskSettableEnv``-style aliases, so an RLlib curriculum callback can advance
  the task between training iterations.
* ``env_config["curriculum_stage"]``                       - initial stage.

The reward explicitly penalises erratic attitude (tilt, angular rate) and jerky
control, to encourage smooth, stable flight.
"""

from __future__ import annotations

from typing import Any

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces

    _GYM_BASE = gym.Env
except Exception:  # pragma: no cover
    gym = None
    spaces = None
    _GYM_BASE = object

from ..physics import MuJoCoQuadEngine

STAGE_HOVER = 1
STAGE_WAYPOINT = 2
VALID_STAGES = (STAGE_HOVER, STAGE_WAYPOINT)


class DroneFlightSchoolEnv(_GYM_BASE):
    """6-DOF quadcopter env with a 2-stage curriculum."""

    metadata = {"render_modes": []}

    def __init__(self, env_config: dict | None = None) -> None:
        super().__init__()
        cfg = dict(env_config or {})

        self.engine = MuJoCoQuadEngine(seed=cfg.get("seed"))
        self._rng = np.random.default_rng(cfg.get("seed"))

        self.stage = int(cfg.get("curriculum_stage", STAGE_HOVER))
        self.max_steps = int(cfg.get("max_steps", 500))
        self.spawn_height = float(cfg.get("spawn_height", 1.5))

        # Reward weights (tuned for stable hover/waypoint).
        self.w_pos = float(cfg.get("w_pos", 1.0))
        self.w_vel = float(cfg.get("w_vel", 0.05))
        self.w_tilt = float(cfg.get("w_tilt", 0.6))      # penalise roll/pitch
        self.w_omega = float(cfg.get("w_omega", 0.04))   # penalise angular rate
        self.w_action = float(cfg.get("w_action", 0.02))  # penalise control jerk
        self.w_yaw = float(cfg.get("w_yaw", 0.05))
        self.alive_bonus = float(cfg.get("alive_bonus", 1.0))
        self.goal_radius = float(cfg.get("goal_radius", 0.5))

        # Safety / termination thresholds.
        self.max_tilt = float(cfg.get("max_tilt", np.deg2rad(80.0)))
        self.min_height = float(cfg.get("min_height", 0.15))
        self.max_pos_error = float(cfg.get("max_pos_error", 8.0))

        self._step_count = 0
        self._target = np.array([0.0, 0.0, self.spawn_height], dtype=np.float64)
        self._prev_action = np.zeros(self.engine.N_MOTORS, dtype=np.float64)

        if spaces is not None:
            self.action_space = spaces.Box(
                low=-1.0, high=1.0, shape=(self.engine.N_MOTORS,), dtype=np.float32
            )
            # obs: [pos_err(3), vel(3), euler(3), ang_vel(3), prev_action(4)] = 16
            obs_dim = 16
            high = np.full(obs_dim, np.inf, dtype=np.float32)
            self.observation_space = spaces.Box(low=-high, high=high, dtype=np.float32)

    # ----------------------- curriculum API --------------------------- #
    def set_stage(self, stage: int) -> None:
        if int(stage) not in VALID_STAGES:
            raise ValueError(f"Invalid curriculum stage {stage}; valid={VALID_STAGES}")
        self.stage = int(stage)

    def get_stage(self) -> int:
        return self.stage

    # Ray RLlib TaskSettableEnv-style aliases.
    def set_task(self, task: int) -> None:
        self.set_stage(task)

    def get_task(self) -> int:
        return self.stage

    def sample_tasks(self, n: int):
        return [int(self._rng.choice(VALID_STAGES)) for _ in range(n)]

    # ------------------------------------------------------------------ #
    def _sample_target(self) -> np.ndarray:
        if self.stage == STAGE_HOVER:
            # Hover at the spawn point.
            return np.array([0.0, 0.0, self.spawn_height], dtype=np.float64)
        # Waypoint: random offset around the spawn, kept within bounds.
        offset = self._rng.uniform(low=[-2.0, -2.0, -0.8], high=[2.0, 2.0, 1.2])
        tgt = np.array([0.0, 0.0, self.spawn_height], dtype=np.float64) + offset
        tgt[2] = max(tgt[2], 0.6)
        return tgt

    def _build_obs(self, state) -> np.ndarray:
        pos_err = self._target - state.position
        obs = np.concatenate(
            [pos_err, state.velocity, state.orientation, state.angular_velocity,
             self._prev_action]
        ).astype(np.float32)
        return obs

    # ------------------------------------------------------------------ #
    def reset(self, *, seed: int | None = None, options: dict | None = None):
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._step_count = 0
        self._prev_action = np.zeros(self.engine.N_MOTORS, dtype=np.float64)

        self._target = self._sample_target()

        # Spawn with a small attitude / position / rate perturbation so the
        # agent must actively stabilise (harder than a perfect start).
        pos = np.array([0.0, 0.0, self.spawn_height]) + self._rng.uniform(-0.15, 0.15, size=3)
        euler0 = self._rng.uniform(-0.12, 0.12, size=3)   # ~+-7 deg
        ang0 = self._rng.uniform(-0.2, 0.2, size=3)
        vel0 = self._rng.uniform(-0.2, 0.2, size=3)

        state = self.engine.reset(
            position=pos, velocity=vel0, orientation=euler0, angular_velocity=ang0
        )
        obs = self._build_obs(state)
        info = {"stage": self.stage, "target": self._target.copy()}
        return obs, info

    # ------------------------------------------------------------------ #
    def step(self, action: np.ndarray):
        self._step_count += 1
        action = np.clip(np.asarray(action, dtype=np.float64).reshape(self.engine.N_MOTORS),
                         -1.0, 1.0)

        # Phase 2: ideal conditions -> no external wind force.
        state = self.engine.step(action, external_force=None)

        pos_err = self._target - state.position
        dist = float(np.linalg.norm(pos_err))
        roll, pitch, yaw = state.orientation

        # --- reward shaping ------------------------------------------- #
        r_pos = -self.w_pos * dist
        r_vel = -self.w_vel * float(np.linalg.norm(state.velocity))
        # erratic-attitude penalty: tilt away from level + body angular rates
        r_tilt = -self.w_tilt * float(roll * roll + pitch * pitch)
        r_yaw = -self.w_yaw * float(yaw * yaw)
        r_omega = -self.w_omega * float(np.sum(np.square(state.angular_velocity)))
        # control-jerk penalty: discourage erratic motor commands
        r_action = -self.w_action * float(np.sum(np.square(action - self._prev_action)))
        reward = self.alive_bonus + r_pos + r_vel + r_tilt + r_yaw + r_omega + r_action

        reached = dist <= self.goal_radius
        if reached:
            reward += 2.0  # bonus for being on target (any stage)

        self._prev_action = action.copy()

        # --- termination ---------------------------------------------- #
        crashed = (
            state.position[2] < self.min_height
            or abs(roll) > self.max_tilt
            or abs(pitch) > self.max_tilt
            or dist > self.max_pos_error
        )
        terminated = bool(crashed)
        if crashed:
            reward -= 50.0

        truncated = bool(self._step_count >= self.max_steps)

        obs = self._build_obs(state)
        info = {
            "stage": self.stage,
            "distance_to_target": dist,
            "tilt_deg": float(np.rad2deg(np.hypot(roll, pitch))),
            "reached": bool(reached),
            "crashed": bool(crashed),
            "target": self._target.copy(),
        }
        return obs, float(reward), terminated, truncated, info
