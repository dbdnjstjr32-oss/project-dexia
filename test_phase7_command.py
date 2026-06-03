"""Phase-7 verification: dynamic C2 deployment via the Object Pool.

Drives the full backend command pipeline WITHOUT resizing any RLlib dict space:
  1. Build DroneMARLEnv with a pool of dormant deployable agents.
  2. Confirm the agent set / action spaces are fixed and the pool starts dormant.
  3. Inject a SPAWN command at [6.0, 6.0] into commands.json (as the HUD would).
  4. Drain + apply commands (exactly as the streamer does each tick) and verify a
     pooled agent is ACTIVATED and teleported to ~[6, 6].
  5. Inject a REMOVE command and verify it returns to the pool (dormant).

Run (Python 3.12 venv — DroneMARLEnv imports Ray):
    .venv312\\Scripts\\python.exe test_phase7_command.py
"""

from __future__ import annotations

import os
import sys
import uuid

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.envs.drone_marl_env import DroneMARLEnv
from dexia.integrations.command_queue import append_command, drain_commands

CMD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.test.json")


def hover_all(env):
    return {aid: env.engines[aid].hover_action for aid in env.possible_agents}


def main() -> int:
    print("=" * 74)
    print("PHASE 7 COMMAND TEST — Object-Pool dynamic spawn / remove")
    print("=" * 74)

    # fresh queue file for the test
    if os.path.exists(CMD_PATH):
        os.remove(CMD_PATH)

    env = DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "pool_size": 10, "seed": 7,
        "enable_wind": False, "enable_baro": False, "enable_sensor_noise": False,
        "enable_aa": True,
        "aa_config": {"position": [5.0, 5.0, 0.0], "radar_range": 3.0, "kill_radius": 1.2},
    })
    env.reset(seed=7)

    n_agents = len(env.possible_agents)
    act_dims = {env.action_spaces[a].shape[0] for a in env.possible_agents}
    print(f"\nFixed agent set      : {n_agents} agents "
          f"({len(env.recon_ids)} recon + {len(env.combat_kami_ids)} combat kami "
          f"+ {len(env.deployable_ids)} pooled)")
    print(f"Action dims (uniform): {act_dims}  -> RLlib spaces never resize")
    print(f"Pool dormant at start: {sum(not env.is_active(a) for a in env.deployable_ids)}"
          f"/{len(env.deployable_ids)} dormant")
    active_before = set(env.active_ids())

    # step once so the sim is running
    env.step(hover_all(env))

    # --- 3) inject SPAWN command at [6,6] (as the HUD POST would) -------- #
    spawn = {
        "id": str(uuid.uuid4()), "action": "spawn",
        "x": 6.0, "y": 6.0, "z": 1.5,
        "lon": 126.9788, "lat": 37.5669,
        "profile": {"name": "Field Kami", "topology": "quad",
                    "mass": 0.7, "arm_length": 0.12, "max_thrust": 8.0, "drag_coeff": 0.1},
    }
    append_command(spawn, path=CMD_PATH)
    print(f"\nInjected SPAWN @ [6.0, 6.0] (id={spawn['id'][:8]})")

    # --- 4) drain + apply (the streamer's per-tick logic) --------------- #
    results = [env.apply_command(c) for c in drain_commands(path=CMD_PATH)]
    print(f"  drained+applied: {results}")

    spawned_id = results[0]["agent_id"] if results and results[0]["ok"] else None
    assert spawned_id is not None, "SPAWN failed to activate a pool agent!"

    # advance a few ticks so it settles
    for _ in range(3):
        env.step(hover_all(env))

    pos = env.engines[spawned_id].get_state().position
    active_after = set(env.active_ids())
    newly = active_after - active_before
    dist = float(np.hypot(pos[0] - 6.0, pos[1] - 6.0))
    print(f"\n[SPAWN verification]")
    print(f"  activated agent       : {spawned_id} (was dormant: {spawned_id in env.deployable_ids})")
    print(f"  newly active id(s)    : {newly}")
    print(f"  position now          : [{pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}] "
          f"(horiz dist to [6,6] = {dist:.2f} m)")
    print(f"  active count          : {len(active_before)} -> {len(active_after)}")
    spawn_ok = (spawned_id in env.deployable_ids and env.is_active(spawned_id)
                and dist < 1.0 and len(active_after) == len(active_before) + 1)
    print(f"  SPAWN: {'PASS' if spawn_ok else 'FAIL'}")

    # --- 5) inject REMOVE and verify return-to-pool --------------------- #
    rm = {"id": str(uuid.uuid4()), "action": "remove", "agent_id": spawned_id}
    append_command(rm, path=CMD_PATH)
    rm_results = [env.apply_command(c) for c in drain_commands(path=CMD_PATH)]
    env.step(hover_all(env))
    remove_ok = (not env.is_active(spawned_id)) and rm_results and rm_results[0]["ok"]
    print(f"\n[REMOVE verification]")
    print(f"  removed {spawned_id}: dormant again = {not env.is_active(spawned_id)}")
    print(f"  REMOVE: {'PASS' if remove_ok else 'FAIL'}")

    if os.path.exists(CMD_PATH):
        os.remove(CMD_PATH)

    ok = spawn_ok and remove_ok
    print("\n" + "=" * 74)
    print(f"PHASE 7 COMMAND TEST: {'PASS' if ok else 'FAIL'}")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
