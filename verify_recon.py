"""HONEST proof of Phase A — AIP recon: terrain-aware route → climb → waypoints →
orbit → DETECTION of a target that was invisible before the sweep.

Nothing about detection is scripted: the UAV starts grounded (terrain-occluded,
cannot see the enemy), the commander orders recon, the AIP plans a route from the
terrain, and the UAV physically climbs and flies it until its sensor — under the
real feed/LOS model — picks the enemy up. Exit 0 = proven, 1 = failed.
"""
from __future__ import annotations
import sys
from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.mission_manager import MissionManager


class StaticRedCommander:
    def step(self, world, tick):
        return


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("=== HONEST RECON PROOF (Phase A) ===\n")
    bg = BattleGenerator(seed=42)
    world = bg.generate({"tb2_recon_uav": 1, "m777_howitzer": 1}, difficulty="easy")
    t = world.terrain

    def Z(x, y):
        return [float(x), float(y), float(t.height(x, y))] if t is not None else [float(x), float(y)]

    tb2 = next(e for e in world.blue if "tb2" in e.cls)
    armor = next(e for e in world.red if e.category in ("armor", "apc"))
    # UAV sits grounded in the south; enemy is far north in the unconfirmed area
    tb2.position = Z(0, -4000)
    armor.position = Z(1500, 5000)
    armor.behavior, armor.route, armor._motion = "static", None, None
    world.entities = [e for e in world.entities if e.side == "blue" or e is armor]

    mm = MissionManager(world, StaticRedCommander())
    spawn_alt = tb2.position[2] if len(tb2.position) > 2 else 0.0
    print(f"UAV {tb2.entity_id} grounded @ {tb2.position[:2]} alt {spawn_alt:.0f} m")
    print(f"Enemy {armor.entity_id} hidden @ {armor.position[:2]}\n")

    # --- baseline: a few ticks with NO recon — must see nothing ---
    for _ in range(3):
        mm._observe()
    pre_tracks = len(mm.fusion.active_tracks())
    print(f"T+{mm.current_tick}: tracks before recon = {pre_tracks} (should be 0 — occluded)")

    # --- commander orders recon; AIP plans the route from terrain ---
    area = (-2000.0, 1000.0, 3000.0, 6000.0)
    print(f"\nCOMMANDER: 정찰 지시 (area {area})")
    asset_id = mm.command_recon(area=area)
    for m in mm.aip_feed[-2:]:
        print(f"  [{m['type']}] {m['message']}")
    wps = mm.world.get(asset_id)._motion.route
    print(f"  planned {len(wps)} waypoints; orbit @ {mm.world.get(asset_id)._motion.orbit['alt']:.0f} m\n")

    detect_tick = None
    max_alt = spawn_alt
    max_wi = 0
    orbiting = False
    for _ in range(900):
        mm._observe()
        uav = mm.world.get(asset_id)
        mm_motion = uav._motion
        max_alt = max(max_alt, uav.position[2] if len(uav.position) > 2 else 0.0)
        max_wi = max(max_wi, mm_motion.wi)
        if mm_motion.orbiting():
            orbiting = True
        tracks = mm.fusion.active_tracks()
        if detect_tick is None and tracks:
            detect_tick = mm.current_tick
            tr = tracks[0]
            print(f"T+{mm.current_tick}: DETECTED {tr.track_id} ({tr.category}) conf {tr.confidence:.2f} "
                  f"| UAV alt {uav.position[2]:.0f} m, waypoint {mm_motion.wi}/{len(wps)}")
        if detect_tick is not None and orbiting:
            break

    print(f"\nfinal: UAV alt {mm.world.get(asset_id).position[2]:.0f} m, "
          f"waypoints passed {max_wi}/{len(wps)}, orbiting={orbiting}")

    print("\n=== VERIFICATION ===")
    checks = {
        "blind before recon (0 tracks)": pre_tracks == 0,
        "UAV climbed to altitude (>+500 m)": max_alt > spawn_alt + 500,
        "UAV flew the planned route (>=2 waypoints)": max_wi >= 2,
        "enemy DETECTED only after the sweep": detect_tick is not None,
        "UAV loitering (orbit) on station": orbiting,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\n=== RESULT:", "RECON PROVEN ✅" if passed else "RECON FAILED ❌", "===")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
