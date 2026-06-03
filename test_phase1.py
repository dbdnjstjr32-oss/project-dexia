"""Phase-1 verification: run one full episode of the Dexia drone wargame env.

This script:
  1. Builds the modular env (3-DOF physics + Gilbert-Elliott comms + wind DR).
  2. Runs ONE complete episode with a simple proportional waypoint controller.
  3. Explicitly TRIGGERS a wind gust mid-episode.
  4. Logs every Gilbert-Elliott state transition.
  5. Renders a Plotly dashboard from the LIVE episode data -> phase1_results.html.

No mock data: every value plotted comes from the simulated rollout.
"""

from __future__ import annotations

import sys

import numpy as np

# Windows consoles often default to a legacy codepage (e.g. cp949) that cannot
# encode the box-drawing / em-dash glyphs below. Force UTF-8 where supported.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.comms import GilbertElliottChannel
from dexia.domain_randomization import WindField
from dexia.envs import DroneWargameEnv
from dexia.physics import Kinematic3DOFEngine
from dexia.viz import EpisodeLog, save_dashboard

SEED = 7
GUST_TRIGGER_STEP = 60          # when we force a gust
GUST_PEAK_FORCE = 2.5           # N (weak gust)
GUST_DURATION = 18              # steps


def proportional_controller(obs: np.ndarray) -> np.ndarray:
    """Tiny P-controller: drive toward the goal using the relative-goal obs.

    obs layout: [rel_goal(3), vel(3), dist_base(1), rssi(1), snr(1),
                 comms_state(1), last_packet_lost(1)]
    """
    rel_goal = obs[0:3]
    vel = obs[3:6]
    kp, kd = 0.08, 0.20
    cmd = kp * rel_goal - kd * vel
    return np.clip(cmd, -1.0, 1.0).astype(np.float32)


def main() -> int:
    print("=" * 70)
    print("DEXIA — Phase 1 verification: 3-DOF drone wargame, single episode")
    print("=" * 70)

    # --- Build modular, seeded components (MARL-ready injection) --------- #
    physics = Kinematic3DOFEngine(dt=0.05, seed=SEED)
    comms = GilbertElliottChannel(
        p_good_to_bad=0.06, p_bad_to_good=0.35, seed=SEED
    )
    wind = WindField(gust_probability=0.0, seed=SEED)  # only manual gusts -> deterministic

    env = DroneWargameEnv(
        physics=physics,
        comms=comms,
        wind=wind,
        goal=np.array([30.0, 25.0, 18.0]),
        base_station=np.array([0.0, 0.0, 0.0]),
        max_steps=400,
        seed=SEED,
    )

    print(f"\nGilbert-Elliott stationary BAD probability: "
          f"{comms.stationary_bad_probability:.3f}")
    print(f"Action space: {env.action_space}")
    print(f"Observation space shape: {env.observation_space.shape}")

    obs, info = env.reset(seed=SEED)
    start_pos = info["position"].copy()
    print(f"\nStart position : {np.round(start_pos, 2)}")
    print(f"Goal           : {np.round(info['goal'], 2)}")
    print(f"Base station   : {np.round(info['base_station'], 2)}")

    log = EpisodeLog(
        goal=info["goal"].copy(),
        base_station=info["base_station"].copy(),
        start=start_pos,
    )

    # log the initial sample (from reset)
    log.record(t=0, pos=info["position"], vel=info["velocity"],
               sample=info["comms"], wind_force=info["wind_force"], reward=0.0)

    prev_state = info["comms"]["state"]
    transitions = []
    total_reward = 0.0
    packets_lost = 0
    gust_fired = False
    terminated = truncated = False
    step = 0

    print("\n--- Running episode ---")
    while not (terminated or truncated):
        step += 1

        # Explicitly trigger a wind gust at the chosen step.
        if step == GUST_TRIGGER_STEP and not gust_fired:
            g = env.wind.trigger_gust(
                direction=np.array([1.0, 1.0, 0.3]),
                peak_force=GUST_PEAK_FORCE,
                duration_steps=GUST_DURATION,
            )
            log.gust_steps.append(step)
            gust_fired = True
            print(f"  [step {step:3d}] >>> WIND GUST TRIGGERED  "
                  f"peak={g.peak_force:.2f} N  dir={np.round(g.direction, 2)}  "
                  f"dur={g.duration_steps} steps")

        action = proportional_controller(obs)
        obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward

        sample = info["comms"]
        log.record(t=step, pos=info["position"], vel=info["velocity"],
                   sample=sample, wind_force=info["wind_force"], reward=reward)

        if sample["packet_lost"]:
            packets_lost += 1

        # Log Gilbert-Elliott state transitions explicitly.
        if sample["state"] != prev_state:
            transitions.append((step, prev_state, sample["state"]))
            print(f"  [step {step:3d}] GE transition: "
                  f"{_state_name(prev_state)} -> {_state_name(sample['state'])}  "
                  f"| RSSI={sample['rssi_dbm']:6.1f} dBm  "
                  f"SNR={sample['snr_db']:5.1f} dB  "
                  f"dist_base={sample['distance_m']:5.1f} m")
            prev_state = sample["state"]

    # --- Summary -------------------------------------------------------- #
    end_reason = "REACHED GOAL" if info["reached_goal"] else (
        "TRUNCATED (max steps)" if truncated else "TERMINATED (out of bounds / goal)"
    )
    print("\n--- Episode summary ---")
    print(f"  Steps run            : {step}")
    print(f"  End condition        : {end_reason}")
    print(f"  Final position       : {np.round(info['position'], 2)}")
    print(f"  Final dist to goal   : {info['distance_to_goal']:.2f} m")
    print(f"  Total reward         : {total_reward:.2f}")
    print(f"  Packets lost         : {packets_lost} / {step} "
          f"({100.0 * packets_lost / max(step, 1):.1f}%)")
    print(f"  GE state transitions : {len(transitions)}")
    print(f"  Wind gust triggered  : {gust_fired} (at step {GUST_TRIGGER_STEP})")

    assert gust_fired, "Wind gust was never triggered!"
    assert len(log.t) == step + 1, "Telemetry log length mismatch."

    # --- Visualization from LIVE data ----------------------------------- #
    out_path = "phase1_results.html"
    save_dashboard(log, out_path,
                   title="Dexia Phase 1 — Live Episode Telemetry "
                         f"(seed={SEED}, {step} steps)")
    print(f"\nPlotly dashboard saved -> {out_path}")
    print("=" * 70)
    print("PHASE 1 VERIFICATION COMPLETE")
    print("=" * 70)
    return 0


def _state_name(s: int) -> str:
    return "GOOD" if int(s) == 0 else "BAD"


if __name__ == "__main__":
    raise SystemExit(main())
