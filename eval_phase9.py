"""Phase-9 evaluation CLI — EpisodeEvalSuite over a checkpoint rollout.

Loads the trained swarm, rolls out one deterministic episode under domain
randomization (reusing the Phase-3 rollout machinery), folds the run into an
:class:`EpisodeRecord`, and scores it with :class:`EpisodeEvalSuite` against the
six thresholded AIP metrics. The verdict + the three immutable audit-trail
summaries are printed and appended to ``evals_results.jsonl`` (which the HUD
Evals panel reads).

Run with the Python 3.12 venv:

    .venv312\\Scripts\\python.exe eval_phase9.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import numpy as np

from dexia.envs.drone_marl_env import DroneMARLEnv, policy_for_agent
from dexia.evals import EpisodeEvalSuite, EpisodeRecord
from eval_phase3 import CHECKPOINT_DIR, load_modules, deterministic_action


def rollout_record(modules: dict, seed: int = 77, max_steps: int = 400,
                   scenario: str = "phase3_dr") -> EpisodeRecord:
    """Roll out one episode and reduce it to an interpreter-agnostic record."""
    env = DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "seed": seed,
        "enable_wind": True, "enable_baro": True, "enable_sensor_noise": True,
        "wind_sigma": 0.5, "gust_prob": 0.03, "max_steps": max_steps,
    })
    obs, _ = env.reset(seed=seed)
    agents = env.possible_agents

    net_surv: list[float] = []
    broadcast_step = kill_step = None
    aa_tracked_peak = 0
    aa_max_ammo = aa_final_ammo = 0
    total_lost = 0

    done, t = False, 0
    while not done and t < max_steps:
        actions = {a: deterministic_action(modules[policy_for_agent(a)], obs[a]) for a in agents}
        obs, _, term, trunc, infos = env.step(actions)
        snap = env.get_full_state()

        team = infos[agents[0]]
        net_surv.append(float(team.get("network_survivability", 0.0)))
        total_lost = int(team.get("total_lost", 0))

        if broadcast_step is None and snap["broadcast"]:
            broadcast_step = t
        if kill_step is None and snap["kill_confirmed"]:
            kill_step = t

        aa = snap.get("aa")
        if aa is not None:
            aa_max_ammo = int(aa.get("max_ammo", aa_max_ammo))
            aa_final_ammo = int(aa.get("ammo", aa_final_ammo))
            aa_tracked_peak = max(aa_tracked_peak, len(team.get("aa", {}).get("tracked", [])))

        done = term["__all__"] or trunc["__all__"]
        t += 1

    lost = env.get_full_state()["lost"]
    recon_survived = sum(1 for a in env.recon_ids if a not in lost)
    kami_survived = sum(1 for a in env.kami_ids if a not in lost)

    return EpisodeRecord(
        scenario=scenario, seed=seed, steps=t,
        n_recon=len(env.recon_ids), n_kami=len(env.kami_ids),
        recon_survived=recon_survived, kami_survived=kami_survived,
        total_lost=total_lost,
        kill_confirmed=bool(env.get_full_state()["kill_confirmed"]),
        broadcast=broadcast_step is not None, broadcast_step=broadcast_step,
        kill_step=kill_step, net_surv_series=net_surv,
        aa_ammo_used=max(0, aa_max_ammo - aa_final_ammo), aa_max_ammo=aa_max_ammo,
        aa_tracked_peak=aa_tracked_peak,
    )


def print_report(report: dict) -> None:
    print("\n" + "-" * 74)
    print(f"  EVAL VERDICT: {report['verdict']}  "
          f"({report['passed']}/{report['evaluated']} metrics passed)")
    print("-" * 74)
    for m in report["metrics"]:
        mark = "PASS" if m["passed"] else ("FAIL" if m["evaluated"] else " n/a")
        op = ">=" if m["direction"] == "max" else "<="
        val = "n/a" if m["value"] is None else f"{m['value']}"
        print(f"  [{mark}] {m['label']:<18} {val:>8} {op} {m['threshold']:<6} "
              f"{m['unit']:<5} · {m['note']}")

    obs = report["observability"]
    print("\n  Audit trails (immutable observability):")
    llm, act, ont = obs["llm"], obs["action"], obs["ontology"]
    print(f"    llm_audit       : {llm['calls']} calls · ok_rate={llm['ok_rate']} "
          f"· mean_latency={llm['mean_latency_ms']} ms · models={llm['models']}")
    print(f"    action_audit    : {act['submitted']} submits · "
          f"accept_rate={act['accept_rate']} · by_action={act['by_action']}")
    print(f"    ontology_state  : objects={ont['objects']} · links={ont['links']}")


def main() -> int:
    print("=" * 74)
    print("DEXIA — Phase 9 evaluation: EpisodeEvalSuite + observability")
    print("=" * 74)

    if not os.path.isdir(CHECKPOINT_DIR):
        print(f"ERROR: checkpoint not found at {CHECKPOINT_DIR}. Run train_phase3.py first.")
        return 1

    print(f"\n[1/3] Loading RLModules from {CHECKPOINT_DIR} ...")
    modules = load_modules(CHECKPOINT_DIR)

    print("[2/3] Rolling out one episode under domain randomization ...")
    ep = rollout_record(modules, seed=77, max_steps=400)
    print(f"      steps={ep.steps} kill={ep.kill_confirmed} lost={ep.total_lost}/{ep.total_drones} "
          f"broadcast@={ep.broadcast_step} recon_alive={ep.recon_survived}/{ep.n_recon}")

    print("[3/3] Scoring with EpisodeEvalSuite (-> evals_results.jsonl) ...")
    report = EpisodeEvalSuite().evaluate(ep)
    print_report(report)

    print("\n" + "=" * 74)
    print(f"PHASE 9 EVALUATION COMPLETE -> {report['verdict']}")
    print("=" * 74)
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
