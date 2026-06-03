"""Phase-3 evaluation: load the trained swarm checkpoint, roll out one episode
under domain randomization, and render a Plotly dashboard of the 6-drone
trajectories + kill-chain (broadcast / strike) events.

Loads the two RLModules directly from the checkpoint (no Ray cluster / env
runners needed) and acts deterministically (distribution mean).

Run with the Python 3.12 venv:

    .venv312\\Scripts\\python.exe eval_phase3.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import torch
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ray.rllib.core.rl_module.rl_module import RLModule
from ray.rllib.core.columns import Columns

from dexia.envs.drone_marl_env import (
    DroneMARLEnv,
    policy_for_agent,
    POLICY_RECON,
    POLICY_KAMI,
)

CHECKPOINT_DIR = os.path.abspath("checkpoints/phase3_marl")
OUT_HTML = "phase3_results.html"
N_MOTORS = 4


def load_modules(ckpt_dir: str) -> dict:
    base = os.path.join(ckpt_dir, "learner_group", "learner", "rl_module")
    modules = {}
    for pid in (POLICY_RECON, POLICY_KAMI):
        path = os.path.join(base, pid)
        modules[pid] = RLModule.from_checkpoint(path)
    return modules


def deterministic_action(module, obs: np.ndarray) -> np.ndarray:
    """Greedy action = mean of the action distribution from forward_inference."""
    batch = {Columns.OBS: torch.from_numpy(obs[None]).float()}
    with torch.no_grad():
        out = module.forward_inference(batch)
    dist_inputs = out[Columns.ACTION_DIST_INPUTS][0].cpu().numpy()
    # For a Box action with a diagonal Gaussian, inputs = [mean(4), log_std(4)].
    mean = dist_inputs[:N_MOTORS]
    return np.clip(mean, -1.0, 1.0).astype(np.float64)


def run_episode(modules: dict, seed: int = 77, max_steps: int = 400) -> dict:
    env = DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "seed": seed,
        # Phase-3 conditions: keep DR ON to show robustness, but trim it a touch
        # so the deterministic policy is not destabilised purely by noise.
        "enable_wind": True, "enable_baro": True, "enable_sensor_noise": True,
        "wind_sigma": 0.5, "gust_prob": 0.03,
        "max_steps": max_steps,
    })
    obs, _ = env.reset(seed=seed)

    agents = env.possible_agents
    traj = {a: [] for a in agents}
    times, net_surv, wind_mean, n_lost = [], [], [], []
    broadcast_step = None
    kill_step = None
    broadcast_pos = None
    kill_pos = None

    done = False
    t = 0
    while not done and t < max_steps:
        actions = {}
        for a in agents:
            pid = policy_for_agent(a)
            actions[a] = deterministic_action(modules[pid], obs[a])
        obs, rew, term, trunc, infos = env.step(actions)

        snap = env.get_full_state()
        for a in agents:
            traj[a].append(snap["positions"][a].copy())
        times.append(t)
        team = infos[agents[0]]
        net_surv.append(team.get("network_survivability", 0.0))
        wind_mean.append(float(np.mean([infos[a]["wind_mag"] for a in agents])))
        n_lost.append(team.get("total_lost", 0))

        if broadcast_step is None and snap["broadcast"]:
            broadcast_step = t
            broadcast_pos = snap["positions"].copy()
        if kill_step is None and snap["kill_confirmed"]:
            kill_step = t
            kill_pos = snap["target"].copy()

        done = term["__all__"] or trunc["__all__"]
        t += 1

    return {
        "agents": agents, "recon_ids": env.recon_ids, "kami_ids": env.kami_ids,
        "traj": {a: np.asarray(v) for a, v in traj.items()},
        "times": times, "net_surv": net_surv, "wind_mean": wind_mean, "n_lost": n_lost,
        "broadcast_step": broadcast_step, "kill_step": kill_step,
        "target": env.target.copy(), "base": env.base_station.copy(),
        "loiter_center": env.loiter_center.copy(), "obs_point": env.observation_point.copy(),
        "steps": t,
    }


def build_dashboard(ep: dict) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=2,
        specs=[[{"type": "scene", "rowspan": 2}, {"type": "xy"}],
               [None, {"type": "xy"}]],
        column_widths=[0.58, 0.42], row_heights=[0.5, 0.5],
        subplot_titles=("Swarm 6-DOF Trajectories (2 recon + 4 kami)",
                        "Comms (network survivability) & Wind |F|",
                        "Min altitude & Drones lost"),
        horizontal_spacing=0.08, vertical_spacing=0.12,
    )

    recon_colors = ["#1f77b4", "#17becf"]
    kami_colors = ["#d62728", "#ff7f0e", "#e377c2", "#8c564b"]

    # 3D trajectories
    for i, a in enumerate(ep["recon_ids"]):
        p = ep["traj"][a]
        fig.add_trace(go.Scatter3d(x=p[:, 0], y=p[:, 1], z=p[:, 2], mode="lines",
                                   line=dict(color=recon_colors[i % len(recon_colors)], width=5),
                                   name=a), row=1, col=1)
        fig.add_trace(go.Scatter3d(x=[p[0, 0]], y=[p[0, 1]], z=[p[0, 2]], mode="markers",
                                   marker=dict(size=4, color=recon_colors[i % len(recon_colors)]),
                                   showlegend=False), row=1, col=1)
    for j, a in enumerate(ep["kami_ids"]):
        p = ep["traj"][a]
        fig.add_trace(go.Scatter3d(x=p[:, 0], y=p[:, 1], z=p[:, 2], mode="lines",
                                   line=dict(color=kami_colors[j % len(kami_colors)], width=4),
                                   name=a), row=1, col=1)
        fig.add_trace(go.Scatter3d(x=[p[0, 0]], y=[p[0, 1]], z=[p[0, 2]], mode="markers",
                                   marker=dict(size=4, color=kami_colors[j % len(kami_colors)]),
                                   showlegend=False), row=1, col=1)

    tgt, base, loi, op = ep["target"], ep["base"], ep["loiter_center"], ep["obs_point"]
    fig.add_trace(go.Scatter3d(x=[tgt[0]], y=[tgt[1]], z=[tgt[2]], mode="markers",
                               marker=dict(size=9, color="red", symbol="x"), name="TARGET"),
                  row=1, col=1)
    fig.add_trace(go.Scatter3d(x=[base[0]], y=[base[1]], z=[base[2]], mode="markers",
                               marker=dict(size=7, color="black", symbol="square"),
                               name="base station"), row=1, col=1)
    fig.add_trace(go.Scatter3d(x=[op[0]], y=[op[1]], z=[op[2]], mode="markers",
                               marker=dict(size=7, color="purple", symbol="diamond"),
                               name="recon obs point"), row=1, col=1)
    fig.add_trace(go.Scatter3d(x=[loi[0]], y=[loi[1]], z=[loi[2]], mode="markers",
                               marker=dict(size=7, color="green", symbol="circle"),
                               name="kami loiter center"), row=1, col=1)

    # Comms + wind
    fig.add_trace(go.Scatter(x=ep["times"], y=ep["net_surv"], mode="lines",
                             name="network survivability", line=dict(color="green")),
                  row=1, col=2)
    fig.add_trace(go.Scatter(x=ep["times"], y=ep["wind_mean"], mode="lines",
                             name="mean wind |F| [N]", line=dict(color="darkorange")),
                  row=1, col=2)
    # Altitude (min across swarm) + losses
    min_alt = [float(min(ep["traj"][a][k, 2] for a in ep["agents"]))
               for k in range(ep["steps"])]
    fig.add_trace(go.Scatter(x=ep["times"], y=min_alt, mode="lines",
                             name="min swarm altitude [m]", line=dict(color="navy")),
                  row=2, col=2)
    fig.add_trace(go.Scatter(x=ep["times"], y=ep["n_lost"], mode="lines",
                             name="drones lost", line=dict(color="crimson", dash="dot")),
                  row=2, col=2)

    # Event markers
    for (label, step, color) in [("BROADCAST", ep["broadcast_step"], "blue"),
                                 ("KILL", ep["kill_step"], "red")]:
        if step is not None:
            for col in (2,):
                fig.add_trace(go.Scatter(x=[step, step], y=[0, max(1.0, max(ep["wind_mean"] + [1]))],
                                         mode="lines", line=dict(color=color, dash="dash"),
                                         name=f"{label} (t={step})"), row=1, col=col)

    fig.update_xaxes(title_text="step", row=1, col=2)
    fig.update_xaxes(title_text="step", row=2, col=2)
    fig.update_scenes(xaxis_title="x [m]", yaxis_title="y [m]", zaxis_title="z [m]",
                      aspectmode="data")
    bc = ep["broadcast_step"]; kl = ep["kill_step"]
    title = (f"Dexia Phase 3 - Swarm MARL + DR | broadcast="
             f"{'t='+str(bc) if bc is not None else 'none'} | "
             f"kill={'t='+str(kl) if kl is not None else 'none'} | "
             f"lost={ep['n_lost'][-1] if ep['n_lost'] else 0}/6")
    fig.update_layout(title=title, height=840,
                      legend=dict(orientation="h", yanchor="bottom", y=-0.06),
                      margin=dict(l=10, r=10, t=70, b=40))
    return fig


def main() -> int:
    print("=" * 78)
    print("DEXIA - Phase 3 evaluation: load swarm checkpoint, roll out, visualize")
    print("=" * 78)

    if not os.path.isdir(CHECKPOINT_DIR):
        print(f"ERROR: checkpoint not found at {CHECKPOINT_DIR}. Run train_phase3.py first.")
        return 1

    print(f"\n[1/3] Loading RLModules from {CHECKPOINT_DIR} ...")
    modules = load_modules(CHECKPOINT_DIR)
    print(f"      Loaded: {list(modules.keys())}")

    print("\n[2/3] Rolling out one episode under domain randomization ...")
    ep = run_episode(modules, seed=77, max_steps=400)
    print(f"      Steps run         : {ep['steps']}")
    print(f"      Broadcast event   : "
          f"{'t='+str(ep['broadcast_step']) if ep['broadcast_step'] is not None else 'NOT triggered'}")
    print(f"      Kill confirmed    : "
          f"{'t='+str(ep['kill_step']) if ep['kill_step'] is not None else 'NOT confirmed'}")
    print(f"      Drones lost       : {ep['n_lost'][-1] if ep['n_lost'] else 0} / 6")
    print(f"      Mean net-surv     : {np.mean(ep['net_surv']):.3f}")
    print(f"      Mean wind |F|     : {np.mean(ep['wind_mean']):.3f} N")

    print("\n[3/3] Rendering Plotly dashboard ...")
    fig = build_dashboard(ep)
    fig.write_html(OUT_HTML, include_plotlyjs="cdn", full_html=True)
    print(f"      Saved -> {os.path.abspath(OUT_HTML)}")

    print("\n" + "=" * 78)
    print("PHASE 3 EVALUATION COMPLETE")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
