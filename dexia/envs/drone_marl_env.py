"""Multi-agent kill-chain environment (Phase 2.5 -> Phase 3 "Swarm MARL + DR").

A Ray RLlib :class:`MultiAgentEnv` with a configurable swarm of heterogeneous
6-DOF quadcopters, each backed by its own :class:`MuJoCoQuadEngine`:

    agent_recon_{i}  ->  policy_recon
        Climb to a high observation point and detect a static ground target.
        Observation = own 6-DOF state + the TRUE target coordinates.

    agent_kami_{j}   ->  policy_kami
        Loiter until a recon DETECTS the target (broadcast). The target slice
        of the observation is ZERO-MASKED until that broadcast. Then strike.

Phase 3 scales this to 2 recon + 4 kami and re-enables *extreme* domain
randomization:

    * Wind         : per-agent OU gust field (high variance) -> external force.
    * Barometric   : altitude-based thrust degradation + random pressure drops.
    * Sensor noise : Gaussian noise injected into the observed 6-DOF state.

Composite team reward (Phase-3 roadmap):

    R_team   = w1*Detection + w2*Kill_Confirmed + w3*Network_Survivability
               - w4*Total_Loss
    R_recon  = R_team - beta*(Exposure_Time + Detection_Risk)   (+ shaping)
    R_kami   = R_team - zeta*(Comms_Quality_Drop + Path_Risk)   (+ shaping)

Agent set is kept static for the whole episode (crashed drones are latched as
"lost" and counted in Total_Loss but keep reporting observations) — this is the
most robust contract for RLlib's MultiAgentEnv.
"""

from __future__ import annotations

import json
import os
from typing import Any

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except Exception:  # pragma: no cover
    gym = None
    spaces = None

from ray.rllib.env.multi_agent_env import MultiAgentEnv

from ..comms import ChannelState, GilbertElliottChannel
from ..domain_randomization import WindField
from ..physics import MuJoCoQuadEngine
from ..wargame import AntiAirBattery

RECON_PREFIX = "agent_recon"
KAMI_PREFIX = "agent_kami"

# AIP Tactical Recipe (doctrine) the physics enforces — Phase 8.6.
_DEFAULT_RECIPES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aip", "recipes.json"
)

POLICY_RECON = "policy_recon"
POLICY_KAMI = "policy_kami"

# Backwards-compatible single-agent ids (Phase 2.5 default swarm = 1 + 1).
AGENT_RECON = f"{RECON_PREFIX}_0"
AGENT_KAMI = f"{KAMI_PREFIX}_0"

OBS_DIM = 16          # own 6-DOF (12) + target slot (3) + flag (1)
N_MOTORS = 4

# Composite-reward weights (sensible Phase-3 initial values).
DEFAULT_REWARD_WEIGHTS = {
    "w1": 10.0,    # Detection event
    "w2": 100.0,   # Kill confirmed
    "w3": 2.0,     # Network survivability (per-step, in [0,1])
    "w4": 50.0,    # Total loss (per newly-lost drone)
    "beta": 0.5,   # recon exposure / detection-risk penalty
    "zeta": 0.5,   # kami comms-drop / path-risk penalty
    "w_shape": 1.0,  # dense navigation shaping (keeps sparse signal learnable)
}


def policy_for_agent(agent_id: str, *args, **kwargs) -> str:
    """Role-prefix policy mapping: all recons share one policy, all kamis another."""
    return POLICY_RECON if agent_id.startswith(RECON_PREFIX) else POLICY_KAMI


class DroneMARLEnv(MultiAgentEnv):
    """Configurable recon + kamikaze swarm on 6-DOF MuJoCo quads with DR."""

    def __init__(self, env_config: dict | None = None) -> None:
        cfg = dict(env_config or {})
        seed = cfg.get("seed")
        self._rng = np.random.default_rng(seed)
        self._base_seed = seed

        self.num_recon = int(cfg.get("num_recon", 2))
        self.num_kami = int(cfg.get("num_kami", 4))
        # Object pool: extra dormant kami agents pre-instantiated at init so the
        # MARL dict spaces NEVER resize mid-episode. A SPAWN command activates +
        # teleports one; a REMOVE returns it to the pool. (pool_size=0 -> off,
        # preserving all earlier phases.)
        self.pool_size = int(cfg.get("pool_size", 0))
        self.recon_ids = [f"{RECON_PREFIX}_{i}" for i in range(self.num_recon)]
        self.combat_kami_ids = [f"{KAMI_PREFIX}_{j}" for j in range(self.num_kami)]
        self.deployable_ids = [
            f"{KAMI_PREFIX}_{self.num_kami + k}" for k in range(self.pool_size)
        ]
        self.kami_ids = self.combat_kami_ids + self.deployable_ids

        # Far off-map, above-floor parking spots for dormant pool agents.
        self._park_pos = {
            aid: np.array([300.0 + 6.0 * k, 300.0, 40.0], dtype=np.float64)
            for k, aid in enumerate(self.deployable_ids)
        }
        self._active: dict[str, bool] = {}        # set in reset()
        self._spawn_meta: dict[str, dict] = {}     # per-agent deploy metadata

        # --- Drone Garage profiles (optional) -------------------------- #
        # Assign a saved airframe profile per role; falls back to the default
        # quad when unset, preserving previous behaviour.
        self.recon_profile = cfg.get("recon_profile")  # dict or None
        self.kami_profile = cfg.get("kami_profile")    # dict or None
        self._profile_for = {
            **{aid: self.recon_profile for aid in self.recon_ids},
            **{aid: self.kami_profile for aid in self.kami_ids},
        }

        # --- AIP doctrine enforcement (Phase 8.6) ---------------------- #
        # The physics dynamically respects the AI-learned Tactical Recipe
        # (dexia/aip/recipes.json): active kamikazes closer than the doctrine's
        # min_scatter_distance_m receive an artificial repulsive force pushing
        # them apart. Opt-out (enforce_doctrine=False) keeps training / earlier
        # phases on their original dynamics.
        self.enforce_doctrine = bool(cfg.get("enforce_doctrine", True))
        self.recipes_path = cfg.get("recipes_path", _DEFAULT_RECIPES_PATH)
        self.doctrine_repel_gain = float(cfg.get("doctrine_repel_gain", 3.0))  # N at contact
        self.doctrine_repel_max = float(cfg.get("doctrine_repel_max", 4.0))    # N cap per drone
        self.current_doctrine: dict = {}
        self.current_doctrine_version = None

        # --- one 6-DOF MuJoCo engine + comms + wind per agent ---------- #
        self.engines: dict[str, MuJoCoQuadEngine] = {}
        self.channels: dict[str, GilbertElliottChannel] = {}
        self.winds: dict[str, WindField] = {}
        self._base_max_thrust: dict[str, float] = {}
        for k, aid in enumerate(self.recon_ids + self.kami_ids):
            esd = None if seed is None else seed + 100 + k
            self.engines[aid] = MuJoCoQuadEngine(profile=self._profile_for[aid], seed=esd)
            self._base_max_thrust[aid] = self.engines[aid].max_thrust
            self.channels[aid] = GilbertElliottChannel(seed=esd)
            self.winds[aid] = WindField(
                ambient_mean=cfg.get("wind_ambient_mean", [0.6, 0.2, 0.0]),
                ambient_sigma=float(cfg.get("wind_sigma", 0.8)),       # high variance
                ambient_theta=float(cfg.get("wind_theta", 0.12)),
                gust_probability=float(cfg.get("gust_prob", 0.04)),
                gust_peak_range=tuple(cfg.get("gust_peak_range", (2.0, 5.0))),
                gust_duration_range=tuple(cfg.get("gust_duration_range", (10, 30))),
                max_ambient_force=float(cfg.get("max_ambient_force", 4.0)),
                seed=esd,
            )

        # --- agent registry (required by MultiAgentEnv new API) -------- #
        self.possible_agents = list(self.recon_ids + self.kami_ids)
        self.agents = list(self.possible_agents)
        self._agent_ids = set(self.possible_agents)

        # --- per-agent spaces (action dim follows each engine's motor count) #
        # Homogeneous within a role (all recons share a profile, all kamis
        # another), so the shared per-role policy sees a consistent action dim.
        obs_space = spaces.Box(low=-np.inf, high=np.inf, shape=(OBS_DIM,), dtype=np.float32)
        self.observation_spaces = {aid: obs_space for aid in self.possible_agents}
        self.action_spaces = {
            aid: spaces.Box(low=-1.0, high=1.0,
                            shape=(self.engines[aid].N_MOTORS,), dtype=np.float32)
            for aid in self.possible_agents
        }

        # --- scenario geometry ----------------------------------------- #
        self.max_steps = int(cfg.get("max_steps", 500))
        self.target = np.asarray(cfg.get("target", [5.0, 5.0, 1.0]), dtype=np.float64)
        self.base_station = np.asarray(cfg.get("base_station", [0.0, 0.0, 0.0]), dtype=np.float64)
        self.obs_height = float(cfg.get("obs_height", 3.0))
        self.detection_radius = float(cfg.get("detection_radius", 4.0))
        self.los_min_altitude = float(cfg.get("los_min_altitude", 2.5))
        self.exposure_radius = float(cfg.get("exposure_radius", 5.0))
        self.strike_radius = float(cfg.get("strike_radius", 0.7))
        self.loiter_center = np.asarray(cfg.get("loiter_center", [-4.0, -4.0, 1.5]), dtype=np.float64)
        self.loiter_radius = float(cfg.get("loiter_radius", 2.5))

        # safety / stability
        self.min_height = float(cfg.get("min_height", 0.15))
        self.max_tilt = float(cfg.get("max_tilt", np.deg2rad(85.0)))
        self.w_tilt = float(cfg.get("w_tilt", 0.4))
        self.w_omega = float(cfg.get("w_omega", 0.03))
        self.loiter_violation_penalty = float(cfg.get("loiter_violation_penalty", 20.0))

        # --- domain randomization toggles + params --------------------- #
        self.enable_wind = bool(cfg.get("enable_wind", True))
        self.enable_baro = bool(cfg.get("enable_baro", True))
        self.enable_sensor_noise = bool(cfg.get("enable_sensor_noise", True))
        self.baro_alt_ref = float(cfg.get("baro_alt_ref", 2.0))
        self.baro_degrade_per_m = float(cfg.get("baro_degrade_per_m", 0.04))
        self.baro_min_scale = float(cfg.get("baro_min_scale", 0.6))
        self.pressure_drop_prob = float(cfg.get("pressure_drop_prob", 0.02))
        self.pressure_drop_mag = float(cfg.get("pressure_drop_mag", 0.25))
        self.noise_std = {
            "pos": float(cfg.get("noise_pos", 0.05)),
            "vel": float(cfg.get("noise_vel", 0.08)),
            "euler": float(cfg.get("noise_euler", 0.03)),
            "ang": float(cfg.get("noise_ang", 0.05)),
        }

        # --- ground threat: Anti-Air battery (Phase 4) ----------------- #
        # Disabled by default so Phase-3 behaviour is unchanged.
        self.enable_aa = bool(cfg.get("enable_aa", False))
        self.aa: AntiAirBattery | None = None
        if self.enable_aa:
            aa_cfg = dict(cfg.get("aa_config", {}))
            self.aa = AntiAirBattery(
                position=aa_cfg.get("position", [0.0, 0.0, 0.0]),
                radar_dir=aa_cfg.get("radar_dir", [0.0, 0.0, 1.0]),
                radar_range=aa_cfg.get("radar_range", 8.0),
                radar_half_angle_deg=aa_cfg.get("radar_half_angle_deg", 75.0),
                fire_cooldown=aa_cfg.get("fire_cooldown", 6),
                kill_radius=aa_cfg.get("kill_radius", 1.5),
                zone_ttl=aa_cfg.get("zone_ttl", 4),
                seed=seed,
            )

        # reward weights
        w = dict(DEFAULT_REWARD_WEIGHTS)
        w.update(cfg.get("reward_weights", {}))
        self.W = w

        # episode state
        self._step_count = 0
        self._broadcast = False
        self._kill_confirmed = False
        self._lost: set[str] = set()
        self._loss_reason: dict[str, str] = {}   # persistent: aid -> "crash"/"anti_air"

        # --- C2 scenario "armed" gate (Phase 9) ------------------------ #
        # When DISARMED, all active drones are frozen at their staged (placed)
        # positions and the AA holds fire — i.e. a placement/build phase. When
        # ARMED (the ACTIVATE button), drones fly under physics and the enemy AA
        # engages. Default True so training / earlier phases are unaffected.
        self._armed = bool(cfg.get("armed", True))
        self._staged_pos: dict[str, np.ndarray] = {}

        super().__init__()

    # ================================================================== #
    # helpers
    # ================================================================== #
    @property
    def observation_point(self) -> np.ndarray:
        return self.target + np.array([0.0, 0.0, self.obs_height])

    def _own_vec(self, state) -> np.ndarray:
        return np.concatenate(
            [state.position, state.velocity, state.orientation, state.angular_velocity]
        )

    def _noisy_own_vec(self, state) -> np.ndarray:
        v = self._own_vec(state)
        if not self.enable_sensor_noise:
            return v
        n = np.empty(12)
        n[0:3] = self._rng.normal(0, self.noise_std["pos"], 3)
        n[3:6] = self._rng.normal(0, self.noise_std["vel"], 3)
        n[6:9] = self._rng.normal(0, self.noise_std["euler"], 3)
        n[9:12] = self._rng.normal(0, self.noise_std["ang"], 3)
        return v + n

    def _build_obs(self) -> dict[str, np.ndarray]:
        obs = {}
        for aid in self.recon_ids:
            st = self.engines[aid].get_state()
            obs[aid] = np.concatenate(
                [self._noisy_own_vec(st), self.target, [float(self._broadcast)]]
            ).astype(np.float32)
        for aid in self.kami_ids:
            st = self.engines[aid].get_state()
            target_slot = self.target if self._broadcast else np.zeros(3)
            obs[aid] = np.concatenate(
                [self._noisy_own_vec(st), target_slot, [float(self._broadcast)]]
            ).astype(np.float32)
        return obs

    def _baro_thrust_scale(self, altitude: float) -> float:
        if not self.enable_baro:
            return 1.0
        scale = 1.0 - self.baro_degrade_per_m * max(0.0, altitude - self.baro_alt_ref)
        if self._rng.random() < self.pressure_drop_prob:
            scale *= (1.0 - self.pressure_drop_mag)   # sudden pressure drop
        return float(np.clip(scale, self.baro_min_scale, 1.0))

    @staticmethod
    def _stability_pen(state, w_tilt, w_omega) -> float:
        roll, pitch, _ = state.orientation
        return -(w_tilt * (roll * roll + pitch * pitch)
                 + w_omega * float(np.sum(np.square(state.angular_velocity))))

    def _crashed(self, state) -> bool:
        roll, pitch, _ = state.orientation
        return bool(state.position[2] < self.min_height
                    or abs(roll) > self.max_tilt or abs(pitch) > self.max_tilt)

    def _is_detection(self, st) -> bool:
        dist = float(np.linalg.norm(st.position - self.target))
        return dist <= self.detection_radius and st.position[2] >= self.los_min_altitude

    # ================================================================== #
    def reset(self, *, seed: int | None = None, options: dict | None = None):
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._step_count = 0
        self._broadcast = False
        self._kill_confirmed = False
        self._lost = set()
        self._loss_reason = {}
        self._spawn_meta = {}
        self.agents = list(self.possible_agents)
        if self.aa is not None:
            self.aa.reset()

        # Active by default = combat agents; pool (deployable) agents start DORMANT.
        self._active = {aid: True for aid in self.possible_agents}
        for aid in self.deployable_ids:
            self._active[aid] = False

        # recons spawn low near origin, spread out; must climb to station.
        for i, aid in enumerate(self.recon_ids):
            base = np.array([0.0 + 1.2 * i, 0.0, 1.0])
            self.engines[aid].reset(
                position=base + self._rng.uniform(-0.15, 0.15, size=3),
                orientation=self._rng.uniform(-0.08, 0.08, size=3),
            )
            self.channels[aid].reset(ChannelState.GOOD)
            self.winds[aid].reset()

        # combat kamis spawn spread inside the loiter zone.
        for j, aid in enumerate(self.combat_kami_ids):
            ang = 2.0 * np.pi * j / max(self.num_kami, 1)
            offs = np.array([np.cos(ang), np.sin(ang), 0.0]) * (self.loiter_radius * 0.5)
            self.engines[aid].reset(
                position=self.loiter_center + offs + self._rng.uniform(-0.2, 0.2, size=3),
                orientation=self._rng.uniform(-0.08, 0.08, size=3),
            )
            self.channels[aid].reset(ChannelState.GOOD)
            self.winds[aid].reset()

        # pool agents parked far off-map (dormant).
        for aid in self.deployable_ids:
            self.engines[aid].reset(position=self._park_pos[aid])
            self.channels[aid].reset(ChannelState.GOOD)
            self.winds[aid].reset()

        # remember where everything is staged (used to freeze while disarmed)
        self._staged_pos = {
            aid: self.engines[aid].get_state().position.copy()
            for aid in self.possible_agents
        }

        # Phase 8.6 — load the active Tactical Recipe so the physics respects it.
        self._load_doctrine()

        infos = {aid: {} for aid in self.possible_agents}
        return self._build_obs(), infos

    # ------------------------------------------------------------------ #
    def _load_doctrine(self) -> None:
        """Read the latest AIP Tactical Recipe (recipes.json) into the env so
        ``step()`` can enforce it. Missing/invalid file -> empty doctrine
        (enforcement becomes a no-op), so the env never hard-fails on it."""
        rules: dict = {}
        version = None
        try:
            with open(self.recipes_path, "r", encoding="utf-8") as f:
                recipe = json.load(f)
            rules = recipe.get("rules", {}) or {}
            version = recipe.get("version")
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            rules = {}
        self.current_doctrine = rules
        self.current_doctrine_version = version

    # ------------------------------------------------------------------ #
    def _doctrine_repulsion(self) -> dict:
        """Phase 8.6 "muscles": a capped horizontal repulsive force for every
        active kamikaze pair closer than the doctrine's min_scatter_distance_m —
        the physical enforcement of the AI-learned dispersion rule. Returns
        {agent_id: force(3,)} to be added to each drone's external force."""
        min_d = float(self.current_doctrine.get("min_scatter_distance_m", 0) or 0.0)
        if not self.enforce_doctrine or min_d <= 0.0 or not self._armed:
            return {}
        kam = [a for a in self.kami_ids if self._active.get(a) and a not in self._lost]
        if len(kam) < 2:
            return {}
        pos = {a: self.engines[a].get_state().position for a in kam}
        forces = {a: np.zeros(3, dtype=np.float64) for a in kam}
        for i in range(len(kam)):
            for j in range(i + 1, len(kam)):
                ai, aj = kam[i], kam[j]
                delta = pos[ai][:2] - pos[aj][:2]          # horizontal separation
                d = float(np.linalg.norm(delta))
                if d >= min_d:
                    continue
                if d < 1e-6:                                # coincident -> deterministic split
                    ang = 2.0 * np.pi * (i + 1) / len(kam)
                    dirxy = np.array([np.cos(ang), np.sin(ang)])
                else:
                    dirxy = delta / d
                mag = self.doctrine_repel_gain * (min_d - d) / min_d   # closeness in (0,1]
                f = np.array([dirxy[0] * mag, dirxy[1] * mag, 0.0])
                forces[ai] += f
                forces[aj] -= f
        # cap each drone's horizontal repulsion so the quad stays controllable
        for a in kam:
            m = float(np.linalg.norm(forces[a][:2]))
            if m > self.doctrine_repel_max:
                forces[a][:2] *= self.doctrine_repel_max / m
        return forces

    # ================================================================== #
    def step(self, action_dict: dict[str, np.ndarray]):
        self._step_count += 1
        W = self.W

        # Phase 8.6 — AIP doctrine "muscles": repulsion forces (from pre-step
        # positions) that physically enforce the learned min_scatter_distance_m.
        repel = self._doctrine_repulsion()

        # --- 1) advance physics for every agent (with DR) -------------- #
        states = {}
        comms = {}
        wind_mags = {}
        for aid in self.possible_agents:
            eng = self.engines[aid]

            # Dormant pool agents: park-and-hold, no DR, no participation.
            if not self._active[aid]:
                states[aid] = eng.reset(position=self._park_pos[aid])
                comms[aid] = self.channels[aid].step(1e6)  # far -> effectively dead link
                wind_mags[aid] = 0.0
                continue

            # DISARMED: active drones are STAGED — frozen at their placed spot
            # (no physics integration) until the operator presses ACTIVATE.
            if not self._armed:
                hold = self._staged_pos.get(aid, eng.get_state().position)
                states[aid] = eng.reset(position=np.asarray(hold, dtype=np.float64).reshape(3))
                dist_base = float(np.linalg.norm(states[aid].position - self.base_station))
                comms[aid] = self.channels[aid].step(dist_base)
                wind_mags[aid] = 0.0
                continue

            act = action_dict.get(aid)
            if act is None:
                act = eng.hover_action
            act = np.asarray(act, dtype=np.float64).reshape(eng.N_MOTORS)

            # Barometric/aero thrust degradation (altitude + pressure drops).
            cur_alt = eng.get_state().position[2]
            eng.max_thrust = self._base_max_thrust[aid] * self._baro_thrust_scale(cur_alt)

            # Wind disturbance.
            if self.enable_wind:
                wf = self.winds[aid].step()
            else:
                wf = None
            wind_mags[aid] = float(np.linalg.norm(wf)) if wf is not None else 0.0

            # Add the doctrine dispersion force (Phase 8.6) onto the wind channel.
            rf = repel.get(aid)
            if rf is not None:
                wf = rf if wf is None else (wf + rf)

            st = eng.step(act, external_force=wf)
            states[aid] = st

            # Comms link to base (Gilbert-Elliott).
            dist_base = float(np.linalg.norm(st.position - self.base_station))
            comms[aid] = self.channels[aid].step(dist_base)

        # --- 2) team-level events -------------------------------------- #
        # Detection / broadcast (any recon).
        detection_event = 0.0
        if not self._broadcast:
            if any(self._is_detection(states[aid]) for aid in self.recon_ids):
                self._broadcast = True
                detection_event = 1.0

        # Kill confirmed (any ACTIVE kami strikes after broadcast).
        kill_event = 0.0
        if self._broadcast and not self._kill_confirmed:
            for aid in self.kami_ids:
                if not self._active[aid]:
                    continue
                if float(np.linalg.norm(states[aid].position - self.target)) <= self.strike_radius:
                    self._kill_confirmed = True
                    kill_event = 1.0
                    break

        # Network survivability (mean over ACTIVE agents with a usable link).
        link_good = {
            aid: (comms[aid].state == ChannelState.GOOD and not comms[aid].packet_lost)
            for aid in self.possible_agents
        }
        active_agents = [a for a in self.possible_agents if self._active[a]]
        network_surv = float(np.mean(
            [1.0 if link_good[a] else 0.0 for a in active_agents]
        )) if active_agents else 1.0

        # Ground threat: AA engages live, ACTIVE drones in its radar cone.
        # Holds fire while DISARMED (placement/build phase).
        aa_destroyed: set[str] = set()
        aa_result = None
        if self.aa is not None and self._armed:
            alive_positions = {
                aid: states[aid].position for aid in self.possible_agents
                if self._active[aid] and aid not in self._lost
            }
            aa_result = self.aa.update(alive_positions)
            aa_destroyed = aa_result["destroyed"]

        # Total loss (newly crashed OR AA-destroyed ACTIVE drones this step).
        # ``self._loss_reason`` is *persistent* (latched) so the HUD / advisory
        # always knows why a drone is down, not just on the tick it happened.
        newly_lost = 0
        for aid in self.possible_agents:
            if aid in self._lost or not self._active[aid]:
                continue
            if aid in aa_destroyed:
                self._lost.add(aid)
                newly_lost += 1
                self._loss_reason[aid] = "anti_air"
            elif self._crashed(states[aid]):
                self._lost.add(aid)
                newly_lost += 1
                self._loss_reason[aid] = "crash"

        # --- 3) composite team reward ---------------------------------- #
        R_team = (
            W["w1"] * detection_event
            + W["w2"] * kill_event
            + W["w3"] * network_surv
            - W["w4"] * float(newly_lost)
        )

        # --- 4) per-agent rewards -------------------------------------- #
        rewards: dict[str, float] = {}

        for aid in self.recon_ids:
            st = states[aid]
            dist_target = float(np.linalg.norm(st.position - self.target))
            d_station = float(np.linalg.norm(st.position - self.observation_point))
            exposure = float(np.clip(1.0 - dist_target / self.exposure_radius, 0.0, 1.0))
            detection_risk = float(np.clip(1.0 - dist_target / self.detection_radius, 0.0, 1.0))
            shaping = (1.0 - 0.3 * d_station) + self._stability_pen(st, self.w_tilt, self.w_omega)
            if self._broadcast:
                shaping += 0.5  # hold-on-station bonus once detected
            r = R_team - W["beta"] * (exposure + detection_risk) + W["w_shape"] * shaping
            rewards[aid] = float(r)

        for aid in self.kami_ids:
            if not self._active[aid]:
                rewards[aid] = 0.0   # dormant pool agent: neutral
                continue
            st = states[aid]
            speed = float(np.linalg.norm(st.velocity))
            roll, pitch, _ = st.orientation
            d_loiter = float(np.linalg.norm(st.position - self.loiter_center))
            d_target = float(np.linalg.norm(st.position - self.target))

            comms_drop = 0.0 if link_good[aid] else 1.0
            path_risk = (roll * roll + pitch * pitch) + 0.05 * speed

            shaping = self._stability_pen(st, self.w_tilt, self.w_omega)
            if not self._broadcast:
                # loiter: stay near center; MASSIVE penalty for leaving the zone.
                shaping += 1.0 - 0.2 * d_loiter
                if d_loiter > self.loiter_radius:
                    shaping -= self.loiter_violation_penalty * (
                        1.0 + (d_loiter - self.loiter_radius)
                    )
                    path_risk += (d_loiter - self.loiter_radius)
            else:
                # strike run: approach the target (kill reward is in R_team).
                shaping += 1.0 - 0.4 * d_target

            r = R_team - W["zeta"] * (comms_drop + path_risk) + W["w_shape"] * shaping
            rewards[aid] = float(r)

        # --- 5) termination / truncation ------------------------------- #
        active_recon = [a for a in self.recon_ids if self._active[a]]
        active_kami = [a for a in self.kami_ids if self._active[a]]
        all_recon_lost = bool(active_recon) and all(a in self._lost for a in active_recon)
        all_kami_lost = bool(active_kami) and all(a in self._lost for a in active_kami)
        episode_done = bool(self._kill_confirmed or all_recon_lost or all_kami_lost)
        truncated_now = bool(self._step_count >= self.max_steps)

        terminateds = {aid: episode_done for aid in self.possible_agents}
        terminateds["__all__"] = episode_done
        truncateds = {aid: truncated_now for aid in self.possible_agents}
        truncateds["__all__"] = truncated_now

        # --- 6) infos (rich, for eval) --------------------------------- #
        infos = {}
        for aid in self.possible_agents:
            infos[aid] = {
                "active": bool(self._active[aid]),
                "deployable": aid in self.deployable_ids,
                "lost": aid in self._lost,
                "loss_reason": self._loss_reason.get(aid),
                "link_good": bool(link_good[aid]),
                "snr_db": float(comms[aid].snr_db),
                "wind_mag": wind_mags[aid],
            }
        # attach team-level telemetry to the first agent's info
        team_info = {
            "armed": bool(self._armed),
            "broadcast": self._broadcast,
            "kill_confirmed": self._kill_confirmed,
            "detection_event": detection_event,
            "kill_event": kill_event,
            "network_survivability": network_surv,
            "total_lost": len(self._lost),
            "newly_lost": newly_lost,
            "R_team": float(R_team),
            # Phase 8.6 — which doctrine is currently driving the physics.
            "doctrine_version": self.current_doctrine_version,
            "min_scatter_distance_m": self.current_doctrine.get("min_scatter_distance_m"),
        }
        if aa_result is not None:
            team_info["aa"] = {
                "tracked": list(aa_result["tracked"]),
                "fired": bool(aa_result["fired"]),
                "engaged": aa_result["engaged"],
                "destroyed": list(aa_destroyed),
                "active_zones": len(aa_result["zones"]),
            }
        infos[self.possible_agents[0]].update(team_info)

        return self._build_obs(), rewards, terminateds, truncateds, infos

    # ------------------------------------------------------------------ #
    def get_full_state(self) -> dict[str, Any]:
        """True (noise-free) snapshot for evaluation/visualization."""
        return {
            "step": self._step_count,
            "positions": {aid: self.engines[aid].get_state().position.copy()
                          for aid in self.possible_agents},
            "broadcast": self._broadcast,
            "kill_confirmed": self._kill_confirmed,
            "target": self.target.copy(),
            "base_station": self.base_station.copy(),
            "loiter_center": self.loiter_center.copy(),
            "lost": set(self._lost),
            "active": {aid: bool(self._active.get(aid, True)) for aid in self.possible_agents},
            "aa": self.aa.telemetry() if self.aa is not None else None,
            # Phase 8.6 — active AIP doctrine driving the physics.
            "current_doctrine_version": self.current_doctrine_version,
            "min_scatter_distance_m": self.current_doctrine.get("min_scatter_distance_m"),
        }

    # ================================================================== #
    # Dynamic C2 — Object Pool deployment API (Phase 7)
    # ================================================================== #
    def is_active(self, aid: str) -> bool:
        return bool(self._active.get(aid, False))

    def active_ids(self) -> list[str]:
        return [a for a in self.possible_agents if self._active.get(a, False)]

    def activate_agent(self, position, profile: dict | None = None, meta: dict | None = None) -> str | None:
        """Activate the first dormant pool agent at ``position``. Returns its id.

        If ``profile`` is given AND it keeps the same motor count as the pooled
        airframe (so the RLlib action space is unchanged), the agent's MuJoCo
        model is rebuilt with that profile; otherwise the pooled airframe is kept.
        """
        free = next((a for a in self.deployable_ids if not self._active[a]), None)
        if free is None:
            return None

        pos = np.asarray(position, dtype=np.float64).reshape(3) if len(np.atleast_1d(position)) == 3 \
            else np.array([float(position[0]), float(position[1]), 1.5], dtype=np.float64)

        if profile is not None:
            try:
                from ..physics import MuJoCoQuadEngine, normalize_profile
                new_eng = MuJoCoQuadEngine(profile=profile)
                if new_eng.N_MOTORS == self.engines[free].N_MOTORS:  # action dim safe
                    self.engines[free] = new_eng
                    self._base_max_thrust[free] = new_eng.max_thrust
                    self._spawn_meta[free] = {"profile": normalize_profile(profile)}
            except Exception:
                pass  # fall back to pooled airframe

        self._active[free] = True
        self._lost.discard(free)
        self._loss_reason.pop(free, None)
        self.engines[free].reset(position=pos)
        self.channels[free].reset(ChannelState.GOOD)
        self.winds[free].reset()
        self._staged_pos[free] = pos.copy()   # frozen here until ACTIVATE
        if meta:
            self._spawn_meta.setdefault(free, {}).update(meta)
        return free

    def deactivate_agent(self, aid: str) -> bool:
        """Remove an agent. Pool agents return to the pool (dormant); combat
        agents are flagged destroyed. Returns True on success."""
        if aid not in self.possible_agents:
            return False
        if aid in self.deployable_ids:
            self._active[aid] = False
            self._lost.discard(aid)
            self._loss_reason.pop(aid, None)
            self._spawn_meta.pop(aid, None)
            self.engines[aid].reset(position=self._park_pos[aid])
            return True
        # combat agent -> flag as destroyed
        if aid not in self._lost:
            self._lost.add(aid)
            self._loss_reason[aid] = "decommissioned"
        return True

    # --- scenario placement (enemy / friendly) + arm gate -------------- #
    def set_armed(self, armed: bool) -> None:
        self._armed = bool(armed)

    def is_armed(self) -> bool:
        return self._armed

    def set_enemy(self, x: float, y: float) -> None:
        """Place the enemy strongpoint: relocate the AA battery + the target."""
        self.target = np.array([float(x), float(y), 1.0], dtype=np.float64)
        if self.aa is not None:
            self.aa.position = np.array([float(x), float(y), 0.0], dtype=np.float64)

    def set_friendly(self, x: float, y: float) -> None:
        """Place the friendly camp: relocate base station + loiter centre."""
        self.base_station = np.array([float(x), float(y), 0.0], dtype=np.float64)
        self.loiter_center = np.array([float(x), float(y), 1.5], dtype=np.float64)

    def clear_scenario(self) -> None:
        """Recall every deployed drone to the pool and disarm."""
        for aid in list(self.deployable_ids):
            if self._active[aid]:
                self.deactivate_agent(aid)
        self._armed = False

    def apply_command(self, cmd: dict) -> dict:
        """Apply a single C2 command dict. Returns a small result record."""
        action = str(cmd.get("action", "")).lower()
        if action in ("spawn", "deploy"):
            x = float(cmd.get("x", 0.0))
            y = float(cmd.get("y", 0.0))
            z = float(cmd.get("z", 0.3))   # staged low so ACTIVATE shows takeoff
            aid = self.activate_agent(
                [x, y, z], profile=cmd.get("profile"),
                meta={"lon": cmd.get("lon"), "lat": cmd.get("lat"),
                      "profile_name": (cmd.get("profile") or {}).get("name")},
            )
            return {"ok": aid is not None, "action": "spawn", "agent_id": aid,
                    "reason": None if aid else "pool_exhausted"}
        if action in ("remove", "delete", "destroy"):
            aid = cmd.get("agent_id")
            ok = self.deactivate_agent(aid) if aid else False
            return {"ok": ok, "action": "remove", "agent_id": aid}
        if action in ("set_enemy", "enemy"):
            self.set_enemy(float(cmd.get("x", 0.0)), float(cmd.get("y", 0.0)))
            return {"ok": True, "action": "set_enemy"}
        if action in ("set_friendly", "friendly", "base"):
            self.set_friendly(float(cmd.get("x", 0.0)), float(cmd.get("y", 0.0)))
            return {"ok": True, "action": "set_friendly"}
        if action in ("arm", "activate"):
            self.set_armed(True)
            return {"ok": True, "action": "arm", "armed": True}
        if action in ("disarm", "standby"):
            self.set_armed(False)
            return {"ok": True, "action": "disarm", "armed": False}
        if action in ("clear", "reset_scenario"):
            self.clear_scenario()
            return {"ok": True, "action": "clear"}
        return {"ok": False, "action": action, "reason": "unknown_action"}
