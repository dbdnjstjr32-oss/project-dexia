# Dexia — Defense-Grade MARL Drone Wargame Simulator

A modular, phase-built simulator for multi-agent reinforcement learning (MARL)
drone wargaming. Physics, comms, domain randomization, and visualization are
decoupled behind stable interfaces so each layer can be upgraded independently.

---

## ⚠️ Runtime split: Python 3.13 (engine) + Python 3.12 venv (Ray)

This is the single most important setup detail in the project.

**Ray (and therefore RLlib) has no Python 3.13 wheels.** On this machine
`pip install "ray[rllib]"` under Python 3.13 fails with:

```
ERROR: Could not find a version that satisfies the requirement ray[rllib]
ERROR: No matching distribution found for ray[rllib]
```

pip on the 3.13 interpreter only accepts `cp313-*` tags, and Ray publishes none.
MuJoCo, by contrast, **does** ship 3.13 wheels and runs fine there. So the
project deliberately uses **two interpreters**:

| Concern | Interpreter | Why |
|---|---|---|
| Base physics engine (MuJoCo 6-DOF), Phase-1 sim, unit smoke tests | **Python 3.13** (system) | MuJoCo 3.9 has `cp313` wheels; no Ray needed here |
| Ray RLlib training (PPO, MultiAgentEnv), Phases 2 / 2.5+ | **Python 3.12 venv** (`.venv312/`) | Ray 2.55 only ships up to `cp312` |

The `dexia/` package itself is interpreter-agnostic — it imports cleanly on both.
Only the *training scripts* (`train_phase2*.py`) require the venv.

### Creating the venv (already done, documented for reproducibility)

The 3.12 venv was created from the existing
`C:\Users\<user>\AppData\Local\Programs\Python\Python312\python.exe`:

```powershell
# 1) create the venv
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv312

# 2) torch CPU wheel from the PyTorch index (NOT mixed with the PyPI install)
.\.venv312\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3) Ray + RLlib + sim deps from PyPI (separate command — different index)
.\.venv312\Scripts\python.exe -m pip install "ray[rllib]" gymnasium mujoco "numpy<2.3" dm_tree pyarrow scipy
```

> **Gotcha that cost us a run in Phase 2:** do **not** put `ray[rllib]` and
> `torch --index-url .../whl/cpu` in the *same* pip command. `--index-url`
> *replaces* PyPI, so Ray won't be found on the PyTorch index. Install torch
> first (its own index), then everything else from PyPI.

### Confirmed working versions (`.venv312/`)

```
ray 2.55.1   gymnasium 1.2.2   mujoco 3.9.0   numpy 2.2.6   torch 2.12.0+cpu
```

### Running things

```powershell
# Phase 1 (3-DOF sim + Plotly) — system Python 3.13 is fine
python test_phase1.py

# Phase 2 / 2.5 (Ray RLlib training) — MUST use the 3.12 venv
.\.venv312\Scripts\python.exe train_phase2.py
.\.venv312\Scripts\python.exe train_phase2_5.py
```

---

## Architecture

```
dexia/
  physics/
    base.py              PhysicsEngine ABC; DroneState (3-DOF), DroneState6DOF
    kinematics_3dof.py   Lightweight NumPy 3-DOF integrator (Phase 1)
    mujoco_engine.py     MuJoCoQuadEngine — 6-DOF quad, official mujoco bindings (Phase 2)
  comms/
    gilbert_elliott.py   Gilbert-Elliott fading channel (RSSI / SNR / packet loss)
  domain_randomization/
    wind.py              OU ambient breeze + triggerable gusts
  envs/
    drone_env.py         DroneWargameEnv — single-agent 3-DOF Gymnasium env (Phase 1)
    drone_env_6dof.py    DroneFlightSchoolEnv — single-agent 6-DOF + curriculum (Phase 2)
    drone_marl_env.py    DroneMARLEnv — Ray MultiAgentEnv, recon + kamikaze (Phase 2.5)
  viz/
    plotter.py           Plotly multi-panel episode dashboard
```

The `PhysicsEngine` ABC is the key seam: the 3-DOF NumPy integrator and the
6-DOF MuJoCo engine are interchangeable, and a MARL env simply holds **one
engine instance per agent**.

## Phase status

| Phase | Title | Deliverable | Status |
|---|---|---|---|
| 1 | Foundations | 3-DOF sim, GE comms, wind, Plotly (`phase1_results.html`) | ✅ |
| 2 | Flight School | 6-DOF MuJoCo + PPO curriculum (`train_phase2.py`) | ✅ |
| 2.5 | Micro-Team Cooperation | `MultiAgentEnv`, recon + kamikaze kill-chain | ✅ |
| 3 | Swarm MARL + DR | 6 agents (2 recon + 4 kami), extreme DR, 2 shared policies | ✅ |
| 4 | Ground Threats + SITL prep | Anti-Air battery + `sitl_bridge.py` (action→PWM) | ✅ |

## Phase 2.5 — Micro-Team Cooperation (kill-chain)

Two heterogeneous agents, two distinct policies:

- **`agent_recon` → `policy_recon`** — climbs to a high observation point and
  must get within a detection radius (with line-of-sight) of a static target.
  Sees its own 6-DOF state **and the true target coordinates**.
- **`agent_kami` → `policy_kami`** — loiters in a safe zone until recon
  *detects* the target, at which point the target coordinates are broadcast
  (unmasked) into its observation. It then strikes the target.
  - **Massive penalty** for leaving the loiter zone *before* the broadcast.
  - **Massive reward** for striking the target *after* the broadcast.

The target slice of `agent_kami`'s observation is **zero-masked** until the
broadcast flag flips — this is the core MARL signal of the kill-chain.
