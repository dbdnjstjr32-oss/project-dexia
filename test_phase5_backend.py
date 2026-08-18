"""Phase-5 backend verification: run the telemetry streamer for 50 ticks and
verify that telemetry.json is created and continuously updated.

Run:
    .venv312\\Scripts\\python.exe test_phase5_backend.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from telemetry_stream import (
    DEFAULT_JSON_PATH,
    JsonFileSink,
    build_record,
    make_env,
    scenario_positions,
)

N_TICKS = 50


def main() -> int:
    print("=" * 72)
    print("PHASE 5 BACKEND TEST - telemetry streamer (50 ticks -> telemetry.json)")
    print("=" * 72)

    path = DEFAULT_JSON_PATH
    if os.path.exists(path):
        os.remove(path)

    sink = JsonFileSink(path)            # force JSON file sink for the test
    env = make_env(seed=5)
    env.reset(seed=5)
    hover = {aid: env.engines[aid].hover_action for aid in env.possible_agents}

    print(f"\nStreaming {N_TICKS} ticks to {path}\n")
    seen_ticks = []
    mtimes = []
    broadcast_tick = None
    aa_kill_tick = None
    snr_samples = []

    for t in range(N_TICKS):
        for aid, pos in scenario_positions(env, t).items():
            if aid not in env._lost:
                env.engines[aid].reset(position=pos)
        _, _, _, _, infos = env.step(hover)
        record = build_record(env, t, infos)
        sink.publish(record)

        # read it straight back from disk to confirm the file is updating
        assert os.path.exists(path), "telemetry.json was not created!"
        with open(path, "r", encoding="utf-8") as f:
            disk = json.load(f)
        seen_ticks.append(disk["tick"])
        mtimes.append(os.path.getmtime(path))
        snr_samples.append(disk["agents"][0]["snr_db"])

        if broadcast_tick is None and disk["events"]["broadcast"]:
            broadcast_tick = t
        if aa_kill_tick is None and disk["aa"] and disk["aa"]["destroyed"]:
            aa_kill_tick = t
            print(f"  tick {t:3d}: AA destroyed {disk['aa']['destroyed']} "
                  f"(loss_reason on agent = "
                  f"{[a['loss_reason'] for a in disk['agents'] if a['lost']]})")
        if broadcast_tick == t:
            print(f"  tick {t:3d}: BROADCAST fired (recon detected target)")

    # ---- verification ----
    n_agents = len(json.load(open(path, encoding="utf-8"))["agents"])
    ticks_ok = seen_ticks == list(range(N_TICKS))
    updating_ok = len(set(mtimes)) > 1            # file mtime advanced over the run
    structure_ok = n_agents == 6

    print("\n[verification]")
    print(f"  telemetry.json exists            : {os.path.exists(path)}")
    print(f"  ticks 0..{N_TICKS-1} all written       : {ticks_ok} "
          f"(last tick on disk = {seen_ticks[-1]})")
    print(f"  file was continuously updated    : {updating_ok} "
          f"({len(set(mtimes))} distinct mtimes)")
    print(f"  agents serialized                : {n_agents} (expected 6)")
    print(f"  BROADCAST event observed         : {broadcast_tick is not None} "
          f"(tick {broadcast_tick})")
    print(f"  AA interception event observed   : {aa_kill_tick is not None} "
          f"(tick {aa_kill_tick})")
    print(f"  SNR telemetry present (agent0)   : {np.mean(snr_samples):.1f} dB mean")

    sample = json.load(open(path, encoding="utf-8"))
    a0 = sample["agents"][0]
    print("\n[sample telemetry record - last tick, agent 0]")
    print(f"  role={a0['role']} alt={a0['alt']:.2f} speed={a0['speed']:.2f} "
          f"snr={a0['snr_db']:.1f} lost={a0['lost']}")
    print(f"  AA position={sample['aa']['position']} "
          f"active_zones={len(sample['aa']['active_zones'])} "
          f"total_lost={sample['events']['total_lost']}")

    ok = ticks_ok and updating_ok and structure_ok
    print("\n" + "=" * 72)
    print(f"PHASE 5 BACKEND TEST: {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
