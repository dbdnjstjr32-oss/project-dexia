"""Phase-2.5 training: multi-agent PPO on the recon + kamikaze kill-chain.

Maps the two heterogeneous agents to two DISTINCT policies and runs a short
5-iteration loop to verify that:
  * the MultiAgentEnv dict observation/action spaces don't crash RLlib,
  * both `policy_recon` and `policy_kami` are actively collecting rollouts and
    being updated (no dictionary dimension mismatches).

Run with the Python 3.12 venv (Ray has no 3.13 wheels):

    .venv312\\Scripts\\python.exe train_phase2_5.py
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
    AGENT_RECON,
    AGENT_KAMI,
)

ENV_NAME = "dexia_marl_killchain"
NUM_ENV_RUNNERS = 4
TRAIN_ITERS = 5

POLICY_RECON = "policy_recon"
POLICY_KAMI = "policy_kami"


def env_creator(env_config):
    cfg = dict(env_config)
    worker_idx = getattr(env_config, "worker_index", 0)
    cfg.setdefault("seed", 2000 + int(worker_idx))
    return DroneMARLEnv(cfg)


def policy_mapping_fn(agent_id, *args, **kwargs):
    return POLICY_RECON if agent_id == AGENT_RECON else POLICY_KAMI


def _get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def main() -> int:
    print("=" * 74)
    print("DEXIA - Phase 2.5 'Micro-Team Cooperation': MARL recon + kamikaze")
    print("=" * 74)

    register_env(ENV_NAME, env_creator)

    print("\n[1/4] Initializing Ray ...")
    ray.init(ignore_reinit_error=True, include_dashboard=False, log_to_driver=False)
    print(f"      Ray {ray.__version__} | CPUs={ray.cluster_resources().get('CPU')} "
          f"| num_env_runners={NUM_ENV_RUNNERS}")

    print("\n[2/4] Building multi-agent PPO config (2 distinct policies) ...")
    config = (
        PPOConfig()
        .environment(ENV_NAME, env_config={
            "num_recon": 1, "num_kami": 1,
            "enable_wind": False, "enable_baro": False, "enable_sensor_noise": False,
        })
        .framework("torch")
        .multi_agent(
            policies={POLICY_RECON, POLICY_KAMI},
            policy_mapping_fn=policy_mapping_fn,
            policies_to_train=[POLICY_RECON, POLICY_KAMI],
        )
        .env_runners(num_env_runners=NUM_ENV_RUNNERS, rollout_fragment_length="auto")
        .training(
            train_batch_size=4000,
            minibatch_size=256,
            num_epochs=5,
            lr=3e-4,
            gamma=0.99,
            lambda_=0.95,
        )
        .resources(num_gpus=0)
        .debugging(log_level="ERROR")
    )

    build = getattr(config, "build_algo", None) or config.build
    algo = build()
    print(f"      Policies: {sorted([POLICY_RECON, POLICY_KAMI])}")
    print(f"      Mapping : {AGENT_RECON} -> {POLICY_RECON} | "
          f"{AGENT_KAMI} -> {POLICY_KAMI}")

    print(f"\n[3/4] Training {TRAIN_ITERS} iterations ...\n")
    hdr = (f"{'iter':>4} | {'episodes':>8} | {'recon_ret':>10} | {'kami_ret':>10} "
           f"| {'recon_loss':>11} | {'kami_loss':>10} | {'time_s':>6}")
    print(hdr)
    print("-" * len(hdr))

    for i in range(1, TRAIN_ITERS + 1):
        t0 = time.time()
        result = algo.train()
        dt = time.time() - t0

        er = result.get("env_runners", {}) or {}
        agent_rets = er.get("agent_episode_returns_mean", {}) or {}
        recon_ret = agent_rets.get(AGENT_RECON)
        kami_ret = agent_rets.get(AGENT_KAMI)
        # `num_episodes` can be 0 early; `num_episodes_lifetime` is cumulative.
        episodes = er.get("num_episodes_lifetime")
        if episodes is None:
            episodes = er.get("num_episodes")

        learners = result.get("learners", {}) or {}
        recon_loss = _get(learners, POLICY_RECON, "total_loss")
        kami_loss = _get(learners, POLICY_KAMI, "total_loss")

        def fmt(x, w, p=2):
            return f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) else f"{'n/a':>{w}}"

        print(f"{i:>4} | {fmt(episodes,8,0)} | {fmt(recon_ret,10)} | {fmt(kami_ret,10)} "
              f"| {fmt(recon_loss,11,4)} | {fmt(kami_loss,10,4)} | {dt:6.1f}")

    # --- explicit verification that BOTH policies collected & trained --- #
    print("\n[4/4] Verifying both policies are active ...")
    learner_keys = sorted(k for k in (result.get("learners", {}) or {}).keys()
                          if k not in ("__all_modules__",))
    print(f"      Learner/policy modules updated this iter: {learner_keys}")
    both_present = POLICY_RECON in learner_keys and POLICY_KAMI in learner_keys
    print(f"      policy_recon present: {POLICY_RECON in learner_keys} | "
          f"policy_kami present: {POLICY_KAMI in learner_keys}")

    ckpt_dir = os.path.abspath("checkpoints/phase2_5_marl")
    os.makedirs(ckpt_dir, exist_ok=True)
    try:
        algo.save(ckpt_dir)
    except Exception:
        algo.save_to_path(ckpt_dir)
    print(f"      Checkpoint saved -> {ckpt_dir}")

    algo.stop()
    ray.shutdown()

    print("\n" + "=" * 74)
    print("PHASE 2.5 VERIFICATION COMPLETE")
    print(f"  MultiAgentEnv dict spaces: OK (no dimension mismatch)")
    print(f"  Both distinct policies collecting rollouts: {both_present}")
    print("=" * 74)
    return 0 if both_present else 1


if __name__ == "__main__":
    raise SystemExit(main())
