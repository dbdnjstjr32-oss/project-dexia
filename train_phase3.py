"""Phase-3 training: swarm MARL (2 recon + 4 kami) under extreme DR.

Two shared policies (all recons -> policy_recon, all kamis -> policy_kami),
trained with PPO across all available hardware threads for a substantial run so
the agents can begin to learn the sparse detection -> broadcast -> strike signal.

Run with the Python 3.12 venv (Ray has no 3.13 wheels):

    .venv312\\Scripts\\python.exe train_phase3.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ray
from ray.rllib.algorithms.ppo import PPOConfig
from ray.tune.registry import register_env

from dexia.envs.drone_marl_env import (
    DroneMARLEnv,
    policy_for_agent,
    POLICY_RECON,
    POLICY_KAMI,
)

ENV_NAME = "dexia_marl_swarm"
TRAIN_ITERS = int(os.environ.get("PHASE3_ITERS", "60"))
CHECKPOINT_DIR = os.path.abspath("checkpoints/phase3_marl")

# Use all available hardware threads for rollouts (leave 1 for the driver/learner).
_CPU = os.cpu_count() or 8
NUM_ENV_RUNNERS = max(2, _CPU - 1)


def env_creator(env_config):
    cfg = dict(env_config)
    worker_idx = getattr(env_config, "worker_index", 0)
    cfg.setdefault("seed", 3000 + int(worker_idx))
    cfg.setdefault("num_recon", 2)
    cfg.setdefault("num_kami", 4)
    return DroneMARLEnv(cfg)


def _get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def main() -> int:
    print("=" * 80)
    print("DEXIA - Phase 3 'Swarm MARL + Domain Randomization' (2 recon + 4 kami)")
    print("=" * 80)

    register_env(ENV_NAME, env_creator)

    print("\n[1/4] Initializing Ray ...")
    ray.init(ignore_reinit_error=True, include_dashboard=False, log_to_driver=False)
    print(f"      Ray {ray.__version__} | CPUs={ray.cluster_resources().get('CPU')} "
          f"| num_env_runners={NUM_ENV_RUNNERS} (all threads)")

    print("\n[2/4] Building swarm multi-agent PPO config ...")
    config = (
        PPOConfig()
        .environment(ENV_NAME, env_config={"num_recon": 2, "num_kami": 4})
        .framework("torch")
        .multi_agent(
            policies={POLICY_RECON, POLICY_KAMI},
            policy_mapping_fn=policy_for_agent,
            policies_to_train=[POLICY_RECON, POLICY_KAMI],
        )
        .env_runners(num_env_runners=NUM_ENV_RUNNERS, rollout_fragment_length="auto")
        .training(
            train_batch_size=max(8000, NUM_ENV_RUNNERS * 600),
            minibatch_size=1000,
            num_epochs=6,
            lr=3e-4,
            gamma=0.99,
            lambda_=0.95,
            entropy_coeff=0.005,
            clip_param=0.2,
        )
        .resources(num_gpus=0)
        .debugging(log_level="ERROR")
    )

    build = getattr(config, "build_algo", None) or config.build
    algo = build()
    print(f"      Policies: {sorted([POLICY_RECON, POLICY_KAMI])} | "
          f"6 agents -> 2 shared policies")

    print(f"\n[3/4] Training {TRAIN_ITERS} iterations ...\n")
    hdr = (f"{'iter':>4} | {'episodes':>8} | {'ep_ret_mean':>11} | {'recon_ret':>10} "
           f"| {'kami_ret':>10} | {'recon_loss':>10} | {'kami_loss':>9} | {'t_s':>5}")
    print(hdr)
    print("-" * len(hdr))

    best_ep_ret = -1e18
    for i in range(1, TRAIN_ITERS + 1):
        t0 = time.time()
        result = algo.train()
        dt = time.time() - t0

        er = result.get("env_runners", {}) or {}
        episodes = er.get("num_episodes_lifetime")
        if episodes is None:
            episodes = er.get("num_episodes")
        ep_ret = er.get("episode_return_mean")
        mod_ret = er.get("module_episode_returns_mean", {}) or {}
        recon_ret = mod_ret.get(POLICY_RECON)
        kami_ret = mod_ret.get(POLICY_KAMI)

        learners = result.get("learners", {}) or {}
        recon_loss = _get(learners, POLICY_RECON, "total_loss")
        kami_loss = _get(learners, POLICY_KAMI, "total_loss")

        if isinstance(ep_ret, (int, float)):
            best_ep_ret = max(best_ep_ret, ep_ret)

        def fmt(x, w, p=2):
            return f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) else f"{'n/a':>{w}}"

        print(f"{i:>4} | {fmt(episodes,8,0)} | {fmt(ep_ret,11)} | {fmt(recon_ret,10)} "
              f"| {fmt(kami_ret,10)} | {fmt(recon_loss,10,3)} | {fmt(kami_loss,9,3)} "
              f"| {dt:5.1f}")

    print("\n[4/4] Saving checkpoint ...")
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    try:
        algo.save(CHECKPOINT_DIR)
    except Exception:
        algo.save_to_path(CHECKPOINT_DIR)
    print(f"      Checkpoint saved -> {CHECKPOINT_DIR}")

    learner_keys = sorted(k for k in (result.get("learners", {}) or {}).keys()
                          if k != "__all_modules__")
    algo.stop()
    ray.shutdown()

    print("\n" + "=" * 80)
    print("PHASE 3 TRAINING COMPLETE")
    print(f"  Swarm: 6 agents (2 recon + 4 kami) | DR: wind + baro + sensor noise = ON")
    print(f"  Policies updated         : {learner_keys}")
    print(f"  Best episode_return_mean : {best_ep_ret:.2f}")
    print(f"  Checkpoint               : {CHECKPOINT_DIR}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
