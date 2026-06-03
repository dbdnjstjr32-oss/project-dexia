"""Verify the interactive scenario-builder + ACTIVATE gate.

Flow:
  1. Build the interactive env (empty field, disarmed).
  2. Place ENEMY (AA) and FRIENDLY (base) via commands.
  3. Deploy 3 drones (staged, grounded).
  4. Step while DISARMED  -> drones must stay FROZEN (no takeoff, no movement).
  5. ACTIVATE -> step      -> drones must TAKE OFF (altitude climbs via physics).

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe test_scenario_builder.py
"""

from __future__ import annotations

import sys
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from telemetry_stream import make_interactive_env, takeoff_action


def step(env, n=1):
    for _ in range(n):
        if env.is_armed():
            acts = {a: takeoff_action(env.engines[a]) for a in env.active_ids()}
            for a in env.possible_agents:
                acts.setdefault(a, env.engines[a].hover_action)
        else:
            acts = {a: env.engines[a].hover_action for a in env.possible_agents}
        env.step(acts)


def main() -> int:
    print("=" * 72)
    print("SCENARIO BUILDER TEST — placement + ACTIVATE physics gate")
    print("=" * 72)

    env = make_interactive_env(seed=5)
    env.reset(seed=5)
    print(f"\nField: pool={len(env.deployable_ids)} | armed={env.is_armed()} "
          f"(should be False) | active drones={len(env.active_ids())} (should be 0)")

    # 1) place enemy + friendly
    env.apply_command({"action": "set_enemy", "x": 6.0, "y": 6.0})
    env.apply_command({"action": "set_friendly", "x": -2.0, "y": -2.0})
    print(f"Enemy(AA) -> {env.aa.position[:2]}  | Friendly(base) -> {env.base_station[:2]}")

    # 2) deploy 3 drones (staged, grounded z~0.3)
    placed = []
    for (x, y) in [(-2.5, -2.0), (-1.5, -2.5), (-2.0, -1.5)]:
        r = env.apply_command({"action": "spawn", "x": x, "y": y})
        placed.append(r["agent_id"])
    print(f"Deployed (staged): {placed}")
    staged_alts = [env.engines[a].get_state().position[2] for a in placed]
    print(f"  staged altitudes: {[round(z,2) for z in staged_alts]} (grounded)")

    # 3) step DISARMED -> must stay frozen
    before = {a: env.engines[a].get_state().position.copy() for a in placed}
    step(env, 15)
    after = {a: env.engines[a].get_state().position.copy() for a in placed}
    max_move = max(float(np.linalg.norm(after[a] - before[a])) for a in placed)
    frozen_ok = max_move < 1e-6
    print(f"\n[DISARMED] 15 ticks -> max drone movement = {max_move:.6f} m "
          f"({'FROZEN ✓' if frozen_ok else 'MOVED ✗'})")

    # 4) ACTIVATE -> drones take off (physics)
    env.apply_command({"action": "arm"})
    print(f"[ACTIVATE] armed={env.is_armed()}")
    alt0 = {a: env.engines[a].get_state().position[2] for a in placed}
    step(env, 40)
    alt1 = {a: env.engines[a].get_state().position[2] for a in placed}
    climbs = {a: alt1[a] - alt0[a] for a in placed}
    took_off = all(alt1[a] > 1.0 for a in placed)   # climbed off the ground
    print(f"  altitude {[round(alt0[a],2) for a in placed]} -> "
          f"{[round(alt1[a],2) for a in placed]} (Δ {[round(climbs[a],2) for a in placed]})")
    print(f"  TOOK OFF (all > 1.0 m): {took_off}")

    # 5) verify
    ok = (
        not bool(make_interactive_env().is_armed())   # default disarmed
        and frozen_ok
        and took_off
        and abs(env.aa.position[0] - 6.0) < 1e-6
        and abs(env.base_station[0] + 2.0) < 1e-6
    )
    print("\n[verification]")
    print(f"  starts DISARMED                     : True")
    print(f"  drones FROZEN while disarmed         : {frozen_ok}")
    print(f"  drones TAKE OFF after ACTIVATE       : {took_off}")
    print(f"  enemy/friendly relocated by commands : "
          f"{abs(env.aa.position[0]-6.0)<1e-6 and abs(env.base_station[0]+2.0)<1e-6}")
    print("\n" + "=" * 72)
    print(f"SCENARIO BUILDER TEST: {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
