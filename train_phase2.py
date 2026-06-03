"""Phase-2 training: PPO + curriculum on the 6-DOF MuJoCo quadcopter.

Runs Ray RLlib PPO for 10 iterations on the "Flight School" env, starting at
the HOVER curriculum stage and advancing to WAYPOINT once the agent is flying
stably. Saves a checkpoint at the end.

Run with the Python 3.12 venv (Ray has no 3.13 wheels):

    .venv312\\Scripts\\python.exe train_phase2.py
"""

from __future__ import annotations

import os
import sys
import time

# Make the local `dexia` package importable from spawned Ray workers too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import ray
from ray.rllib.algorithms.ppo import PPOConfig
from ray.tune.registry import register_env

from dexia.envs.drone_env_6dof import (
    DroneFlightSchoolEnv,
    STAGE_HOVER,
    STAGE_WAYPOINT,
)

ENV_NAME = "dexia_flight_school"
NUM_ENV_RUNNERS = 16          # leverage local hardware for fast rollouts
TRAIN_ITERS = 10
ADVANCE_RETURN_THRESHOLD = 350.0   # advance HOVER -> WAYPOINT above this return


def env_creator(env_config):
    """Factory used by RLlib. Seeds each runner differently for diversity."""
    cfg = dict(env_config)
    worker_idx = getattr(env_config, "worker_index", 0)
    cfg.setdefault("seed", 1000 + int(worker_idx))
    cfg.setdefault("curriculum_stage", STAGE_HOVER)
    cfg.setdefault("max_steps", 500)
    return DroneFlightSchoolEnv(cfg)


def extract_return(result: dict):
    """Pull the mean episode return across Ray-version metric layouts."""
    er = result.get("env_runners", {}) or {}
    for key in ("episode_return_mean", "episode_reward_mean"):
        if key in er and er[key] is not None:
            return er[key]
    for key in ("episode_return_mean", "episode_reward_mean"):
        if result.get(key) is not None:
            return result[key]
    return None


def extract_len(result: dict):
    er = result.get("env_runners", {}) or {}
    for key in ("episode_len_mean", "episode_length_mean"):
        if er.get(key) is not None:
            return er[key]
    return None


def advance_curriculum(algo, new_stage: int) -> bool:
    """Set the curriculum stage on every env across all runners (best-effort)."""
    def _set(env):
        # env may be wrapped; unwrap to our base env if needed.
        target = getattr(env, "unwrapped", env)
        if hasattr(target, "set_stage"):
            target.set_stage(new_stage)

    grp = getattr(algo, "env_runner_group", None) or getattr(algo, "workers", None)
    if grp is None:
        return False
    try:
        grp.foreach_env(_set)
        return True
    except Exception as exc:  # pragma: no cover
        print(f"  [curriculum] foreach_env failed: {exc}")
        return False


def main() -> int:
    print("=" * 72)
    print("DEXIA - Phase 2 'Flight School': PPO + curriculum on 6-DOF MuJoCo quad")
    print("=" * 72)

    register_env(ENV_NAME, env_creator)

    print("\n[1/4] Initializing Ray ...")
    ray.init(ignore_reinit_error=True, include_dashboard=False, log_to_driver=False)
    res = ray.cluster_resources()
    print(f"      Ray {ray.__version__} | CPUs={res.get('CPU')} | "
          f"num_env_runners={NUM_ENV_RUNNERS}")

    print("\n[2/4] Building PPO config (new API stack) ...")
    config = (
        PPOConfig()
        .environment(ENV_NAME, env_config={"curriculum_stage": STAGE_HOVER})
        .framework("torch")
        .env_runners(
            num_env_runners=NUM_ENV_RUNNERS,
            rollout_fragment_length="auto",
        )
        .training(
            train_batch_size=8000,
            minibatch_size=500,
            num_epochs=10,
            lr=3e-4,
            gamma=0.99,
            lambda_=0.95,
            entropy_coeff=0.0,
            vf_loss_coeff=0.5,
            clip_param=0.2,
        )
        .resources(num_gpus=0)
        .debugging(log_level="ERROR")
    )

    # build_algo() is the current API; fall back to build() on older Ray.
    build = getattr(config, "build_algo", None) or config.build
    algo = build()
    print("      PPO algorithm built.")

    print(f"\n[3/4] Training {TRAIN_ITERS} iterations "
          f"(start stage = HOVER) ...\n")
    header = f"{'iter':>4} | {'stage':<8} | {'ep_return_mean':>14} | {'ep_len_mean':>11} | {'time_s':>7}"
    print(header)
    print("-" * len(header))

    current_stage = STAGE_HOVER
    for i in range(1, TRAIN_ITERS + 1):
        t0 = time.time()
        result = algo.train()
        dt = time.time() - t0

        ret = extract_return(result)
        ep_len = extract_len(result)
        stage_name = "HOVER" if current_stage == STAGE_HOVER else "WAYPOINT"
        ret_str = f"{ret:14.2f}" if ret is not None else f"{'n/a':>14}"
        len_str = f"{ep_len:11.1f}" if ep_len is not None else f"{'n/a':>11}"
        print(f"{i:>4} | {stage_name:<8} | {ret_str} | {len_str} | {dt:7.1f}")

        # Curriculum advancement: HOVER -> WAYPOINT once flying stably.
        if (current_stage == STAGE_HOVER and ret is not None
                and ret >= ADVANCE_RETURN_THRESHOLD):
            if advance_curriculum(algo, STAGE_WAYPOINT):
                current_stage = STAGE_WAYPOINT
                print(f"  >>> Curriculum advanced: HOVER -> WAYPOINT "
                      f"(ep_return_mean={ret:.1f} >= {ADVANCE_RETURN_THRESHOLD})")

    print("\n[4/4] Saving checkpoint ...")
    ckpt_dir = os.path.abspath("checkpoints/phase2_ppo")
    os.makedirs(ckpt_dir, exist_ok=True)
    saved_path = None
    try:
        save_res = algo.save(ckpt_dir)
        saved_path = getattr(save_res, "checkpoint", None)
        saved_path = getattr(saved_path, "path", None) or ckpt_dir
    except Exception:
        # newer API
        algo.save_to_path(ckpt_dir)
        saved_path = ckpt_dir
    print(f"      Checkpoint saved -> {saved_path}")

    algo.stop()
    ray.shutdown()
    print("\n" + "=" * 72)
    print("PHASE 2 TRAINING COMPLETE")
    print(f"  Final curriculum stage : "
          f"{'HOVER' if current_stage == STAGE_HOVER else 'WAYPOINT'}")
    print(f"  Checkpoint             : {saved_path}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
