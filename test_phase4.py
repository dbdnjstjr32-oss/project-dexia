"""Phase-4 verification: Anti-Air ground threat + SITL bridge.

Part A — AA threat:
  Build the swarm env with the Anti-Air battery enabled, then fly one kamikaze
  drone along a scripted path INTO the radar cone / threat zone. Verify that the
  battery detects it, fires, the drone is destroyed, and the Total_Loss penalty
  fires in the composite reward.

Part B — SITL bridge:
  Instantiate the SITL bridge, feed it dummy policy action vectors in [-1, 1],
  and print the translated PWM signals [1000, 2000] us (plus a MAVLink-style
  message and a dry-run UDP send).

Run with the Python 3.12 venv:
    .venv312\\Scripts\\python.exe test_phase4.py
"""

from __future__ import annotations

import sys

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.envs.drone_marl_env import DroneMARLEnv
from dexia.sitl_bridge import (
    SITLBridge,
    MockUDPLink,
    action_to_pwm,
    pwm_to_action,
    to_mavlink_rc_override,
)

TARGET_DRONE = "agent_kami_0"


def part_a_anti_air() -> bool:
    print("=" * 76)
    print("PART A — Anti-Air ground threat")
    print("=" * 76)

    env = DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "seed": 11,
        # ideal conditions so the AA logic is isolated (no DR confounders)
        "enable_wind": False, "enable_baro": False, "enable_sensor_noise": False,
        "enable_aa": True,
        # Battery defends the mission target (~[5,5,1]); placed away from the
        # recon/kami spawn points so only the scripted intruder is engaged.
        "aa_config": {
            "position": [4.0, 4.0, 0.0],
            "radar_dir": [0.0, 0.0, 1.0],
            "radar_range": 8.0,
            "radar_half_angle_deg": 75.0,
            "fire_cooldown": 6,
            "kill_radius": 1.5,
            "zone_ttl": 4,
        },
    })
    obs, _ = env.reset(seed=11)
    aa = env.aa.telemetry()
    print(f"\nAA battery @ {aa['position']} | radar range={aa['radar_range']} m | "
          f"cone half-angle={aa['radar_half_angle_deg']:.0f} deg | "
          f"kill_radius={env.aa.kill_radius} m")
    print(f"Scripted intruder: {TARGET_DRONE} flies from the loiter zone toward "
          f"the defended target.\n")

    # Scripted approach path (world XYZ): from the loiter zone, inbound & overhead.
    path = [np.array([-4.0, -4.0, 2.0]) + (np.array([8.0, 8.0, 0.0]) * t)
            for t in np.linspace(0.0, 1.0, 12)]

    hover = {aid: env.engines[aid].hover_action for aid in env.possible_agents}

    print(f"{'step':>4} | {'intruder pos':>20} | {'range':>6} | {'in_cone':>7} "
          f"| {'fired':>5} | {'destroyed':>9} | {'kami0_rew':>9} | {'R_team':>8}")
    print("-" * 96)

    destroyed_step = None
    kill_reason = None
    kill_newly_lost = None
    kill_R_team = None
    for k, p in enumerate(path):
        # place the intruder at the scripted point (others hover in place)
        env.engines[TARGET_DRONE].reset(position=p)
        obs, rew, term, trunc, infos = env.step(hover)

        team = infos[env.possible_agents[0]]
        aa_info = team.get("aa", {})
        r = float(np.linalg.norm(p - env.aa.position))
        in_cone = env.aa.in_radar(p)
        fired = aa_info.get("fired", False)
        destroyed = aa_info.get("destroyed", [])

        print(f"{k:>4} | {np.array2string(p, precision=1, floatmode='fixed'):>20} "
              f"| {r:6.2f} | {str(in_cone):>7} | {str(fired):>5} "
              f"| {str(destroyed):>22} | {rew[TARGET_DRONE]:9.2f} | {team['R_team']:8.2f}")

        if destroyed_step is None and TARGET_DRONE in destroyed:
            destroyed_step = k
            kill_reason = infos[TARGET_DRONE]["loss_reason"]   # captured AT kill step
            kill_newly_lost = team["newly_lost"]
            kill_R_team = team["R_team"]
            print(f"     >>> {TARGET_DRONE} DESTROYED by AA at step {k} "
                  f"(loss_reason={kill_reason}, newly_lost={kill_newly_lost}, "
                  f"total_lost={team['total_lost']}, R_team={kill_R_team:.2f})")
        if destroyed_step is not None and k >= destroyed_step + 1:
            break

    # --- verification (captured at the destruction step) ---
    ok = (
        destroyed_step is not None
        and infos[TARGET_DRONE]["lost"]      # stays latched as lost afterwards
        and kill_reason == "anti_air"
        and kill_newly_lost >= 1
    )
    print(f"\n[Part A verification]")
    print(f"  intruder destroyed by AA   : {destroyed_step is not None} (step {destroyed_step})")
    print(f"  stays latched as 'lost'    : {infos[TARGET_DRONE]['lost']}")
    print(f"  loss_reason == 'anti_air'  : {kill_reason == 'anti_air'}")
    print(f"  Total_Loss penalty fired   : R_team={kill_R_team} at kill step "
          f"(= w3*net_surv - w4*newly_lost; newly_lost={kill_newly_lost})")
    print(f"  PART A: {'PASS' if ok else 'FAIL'}")
    return ok


def part_b_sitl_bridge() -> bool:
    print("\n" + "=" * 76)
    print("PART B — SITL bridge: RL action [-1,1] -> PWM [1000,2000] us")
    print("=" * 76)

    # Direct conversion sanity checks (the standard quad PWM band).
    print("\nDirect action_to_pwm() conversion:")
    cases = {
        "min   [-1,-1,-1,-1]": np.array([-1.0, -1.0, -1.0, -1.0]),
        "mid   [ 0, 0, 0, 0]": np.array([0.0, 0.0, 0.0, 0.0]),
        "max   [ 1, 1, 1, 1]": np.array([1.0, 1.0, 1.0, 1.0]),
        "dummy [-1,-.5,0,1] ": np.array([-1.0, -0.5, 0.0, 1.0]),
        "clip  [-3, 2,...]  ": np.array([-3.0, 2.0, 0.25, -0.25]),
    }
    for label, act in cases.items():
        print(f"  {label} -> PWM {action_to_pwm(act).tolist()} us")

    # Round-trip check.
    dummy = np.array([-1.0, -0.5, 0.0, 1.0])
    pwm = action_to_pwm(dummy)
    recovered = pwm_to_action(pwm)
    print(f"\nRound-trip pwm_to_action({pwm.tolist()}) -> "
          f"{np.array2string(recovered, precision=3, floatmode='fixed')}")

    # Bridge with a mock UDP link (PX4 default transport), dry-run.
    print("\nSITLBridge over MockUDPLink (PX4-style UDP, dry-run):")
    bridge = SITLBridge(link=MockUDPLink(host="127.0.0.1", port=14550, dry_run=True),
                        module=None, n_motors=4)
    print(f"  bridge.status() = {bridge.status()}")

    result = bridge.send_action(dummy)
    print(f"  send_action({dummy.tolist()}):")
    print(f"    PWM           = {result['pwm'].tolist()} us")
    print(f"    bytes_sent    = {result['bytes_sent']} (dry-run, buffered)")
    mav = result["mavlink"]
    print(f"    MAVLink msg   = {mav['mavpackettype']} "
          f"chan1..4 = [{mav['chan1_raw']}, {mav['chan2_raw']}, "
          f"{mav['chan3_raw']}, {mav['chan4_raw']}]")
    print(f"    link packets_sent = {bridge.link.packets_sent}, "
          f"connected = {bridge.link.is_connected}")

    expected = [1000, 1250, 1500, 2000]
    ok = result["pwm"].tolist() == expected
    print(f"\n[Part B verification]")
    print(f"  dummy [-1,-0.5,0,1] -> {result['pwm'].tolist()} (expected {expected})")
    print(f"  PART B: {'PASS' if ok else 'FAIL'}")
    bridge.link.close()
    return ok


def main() -> int:
    a_ok = part_a_anti_air()
    b_ok = part_b_sitl_bridge()

    print("\n" + "=" * 76)
    print("PHASE 4 VERIFICATION SUMMARY")
    print(f"  Part A (Anti-Air threat + Total_Loss penalty) : {'PASS' if a_ok else 'FAIL'}")
    print(f"  Part B (SITL bridge action->PWM translation)  : {'PASS' if b_ok else 'FAIL'}")
    print(f"  OVERALL: {'PASS' if (a_ok and b_ok) else 'FAIL'}")
    print("=" * 76)
    return 0 if (a_ok and b_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
