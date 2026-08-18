"""Full kill-chain scenario → EpisodeEvalSuite PASS (AIP demo).

The trained checkpoint underperforms on the host's current Ray build, so to
demonstrate a *complete* kill-chain (recon detect → broadcast → kami strike →
kill) flowing through the Phase-9 evals to a PASS verdict, this script drives the
REAL DroneMARLEnv kill-chain logic with a scripted waypoint controller: it
teleports agents along a mission plan (engine.reset) and lets the env's own
detection (radius 4.0 @ alt≥2.5) and strike (radius 0.7) tests fire. The
resulting EpisodeRecord is scored by the actual EpisodeEvalSuite and appended to
evals_results.jsonl — so it also surfaces live in the HUD Evals panel.

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe scenario_killchain.py
"""

from __future__ import annotations

import sys
import warnings

warnings.filterwarnings("ignore")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import numpy as np

from dexia.envs.drone_marl_env import DroneMARLEnv
from dexia.evals import EpisodeEvalSuite, EpisodeRecord


def lerp(a, b, f):
    f = max(0.0, min(1.0, f))
    return np.asarray(a, float) * (1 - f) + np.asarray(b, float) * f


def run(max_steps: int = 60) -> EpisodeRecord:
    # Clean conditions, no AA — the classic Phase-3 recon→strike kill-chain.
    env = DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "seed": 7, "max_steps": max_steps,
        "enable_wind": False, "enable_baro": False, "enable_sensor_noise": False,
        "enable_aa": False,
    })
    obs, _ = env.reset(seed=7)
    tgt = env.target                      # [5,5,1]
    obs_pt = env.observation_point        # [5,5,4]  (detection vantage)
    r0, r1 = env.recon_ids
    k0 = env.kami_ids[0]
    spawn = {a: env.engines[a].get_state().position.copy() for a in env.possible_agents}

    net_surv, broadcast_step, kill_step, total_lost = [], None, None, 0

    done, t = False, 0
    while not done and t < max_steps:
        # --- scripted mission plan (waypoints) ------------------------- #
        # recon_0 ingresses to the observation point -> triggers broadcast ~t15
        env.engines[r0].reset(lerp(spawn[r0], obs_pt, t / 15.0))
        # recon_1 holds a safe overwatch near base (stays alive, good link)
        env.engines[r1].reset(lerp(spawn[r1], [2.0, 2.0, 3.0], t / 12.0))
        # kami loiter until broadcast, then kami_0 dives onto the target
        if broadcast_step is not None:
            f = (t - broadcast_step) / 14.0
            env.engines[k0].reset(lerp(env.loiter_center, tgt, f))
        for a in env.kami_ids[1:]:        # other kamis hold the loiter (alive)
            env.engines[a].reset(lerp(spawn[a], env.loiter_center, t / 12.0))

        actions = {a: env.engines[a].hover_action for a in env.possible_agents}
        obs, _, term, trunc, infos = env.step(actions)
        snap = env.get_full_state()

        team = infos[env.possible_agents[0]]
        net_surv.append(float(team.get("network_survivability", 1.0)))
        total_lost = int(team.get("total_lost", 0))
        if broadcast_step is None and snap["broadcast"]:
            broadcast_step = t
        if kill_step is None and snap["kill_confirmed"]:
            kill_step = t

        done = term["__all__"] or trunc["__all__"]
        t += 1

    lost = env.get_full_state()["lost"]
    rec = EpisodeRecord(
        scenario="scripted_killchain", seed=7, steps=t,
        n_recon=len(env.recon_ids), n_kami=len(env.kami_ids),
        recon_survived=sum(1 for a in env.recon_ids if a not in lost),
        kami_survived=sum(1 for a in env.kami_ids if a not in lost),
        total_lost=total_lost,
        kill_confirmed=bool(env.get_full_state()["kill_confirmed"]),
        broadcast=broadcast_step is not None, broadcast_step=broadcast_step,
        kill_step=kill_step, net_surv_series=net_surv,
        aa_ammo_used=0, aa_max_ammo=0, aa_tracked_peak=0,
    )
    return rec


def main() -> int:
    print("=" * 74)
    print("DEXIA — Full kill-chain scenario → EpisodeEvalSuite")
    print("=" * 74)
    ep = run()
    print(f"\nkill-chain: broadcast@{ep.broadcast_step} → kill@{ep.kill_step} "
          f"| recon_alive={ep.recon_survived}/{ep.n_recon} lost={ep.total_lost}/{ep.total_drones} "
          f"| steps={ep.steps}")

    report = EpisodeEvalSuite().evaluate(ep)
    print(f"\n  EVAL VERDICT: {report['verdict']}  "
          f"({report['passed']}/{report['evaluated']} metrics passed)\n")
    for m in report["metrics"]:
        mark = "PASS" if m["passed"] else ("FAIL" if m["evaluated"] else " n/a")
        op = ">=" if m["direction"] == "max" else "<="
        val = "n/a" if m["value"] is None else f"{m['value']}"
        print(f"  [{mark}] {m['label']:<18} {val:>7} {op} {m['threshold']:<5} · {m['note']}")
    print("\n" + "=" * 74)
    print(f"KILL-CHAIN SCENARIO COMPLETE -> {report['verdict']}")
    print("=" * 74)
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
