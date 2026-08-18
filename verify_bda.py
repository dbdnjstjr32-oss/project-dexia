"""HONEST proof of Phase C — sensor-confirmed BDA via re-recon.

Two enemy vehicles are detected. We strike ONE. After the round lands we fly a
re-recon and let the AIP judge each — purely from whether a sensor still detects
the track (NOT from the world's ground-truth alive flag). A correct verdict must
call the struck unit destroyed AND the untouched bystander survived; getting both
right is what proves the BDA is real sensing, not a coin flip. Exit 0 = proven.
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

    print("=== HONEST BDA PROOF (Phase C) ===\n")
    bg = BattleGenerator(seed=42)
    world = bg.generate({"tb2_recon_uav": 1, "m777_howitzer": 1}, difficulty="easy")
    t = world.terrain

    def Z(x, y):
        return [float(x), float(y), float(t.height(x, y))] if t is not None else [float(x), float(y)]

    tb2 = next(e for e in world.blue if "tb2" in e.cls)
    m777 = next(e for e in world.blue if e.cls.startswith("m777"))
    reds = [e for e in world.red if e.category in ("armor", "apc")]
    tgt, bystander = reds[0], reds[1]
    tb2.position = Z(0, -4000)
    m777.position = Z(0, -1500)
    tgt.position = Z(1500, 5000)        # we will strike this one
    bystander.position = Z(1500, 5300)  # 300 m away — outside the 70 m lethal radius
    for e in (tgt, bystander):
        e.behavior, e.route, e._motion = "static", None, None
    keep = {tgt.entity_id, bystander.entity_id}
    world.entities = [e for e in world.entities if e.side == "blue" or e.entity_id in keep]

    mm = MissionManager(world, StaticRedCommander())
    print(f"target={tgt.entity_id}@{tgt.position[:2]}  bystander={bystander.entity_id}@{bystander.position[:2]}\n")

    # --- recon to detect both ---
    mm.command_recon(area=(-2000.0, 1000.0, 3000.0, 6000.0))
    for _ in range(900):
        mm._observe()
        if len([t for t in mm.fusion.active_tracks() if t.category in ("armor", "apc")]) >= 2:
            break

    def nearest_track(pos):
        cands = [t for t in mm.fusion.active_tracks() if t.category in ("armor", "apc")]
        return min(cands, key=lambda t: (t.position[0]-pos[0])**2 + (t.position[1]-pos[1])**2)
    tgt_tid = nearest_track(tgt.position).track_id
    bys_tid = nearest_track(bystander.position).track_id
    print(f"T+{mm.current_tick}: detected both — target track {tgt_tid}, bystander track {bys_tid}")
    if tgt_tid == bys_tid:
        print("tracks merged — cannot run discrimination test"); return 1

    # --- strike only the target ---
    print(f"\nCOMMANDER: fire on {tgt_tid}")
    mm.command_fires(tgt_tid, m777.entity_id)
    impact_deadline = mm.current_tick + 45
    while mm.current_tick < impact_deadline:
        mm._observe()

    # --- re-recon ("타격 이후 정찰을 한번 더") then let coverage rebuild ---
    print(f"T+{mm.current_tick}: 타격 후 재정찰")
    mm.command_recon(area=(-2000.0, 1000.0, 3000.0, 6000.0))
    for _ in range(40):
        mm._observe()

    print(f"\nCOMMANDER: BDA 요청 (T+{mm.current_tick})")
    verdict = mm.assess_bda()
    for m in mm.aip_feed[-len(verdict):]:
        print("  " + m["message"])

    # ground truth ONLY for test sanity (the verdict itself never reads this)
    truth = {tgt.entity_id: tgt.alive, bystander.entity_id: bystander.alive}
    print(f"\n(ground-truth sanity: {tgt.entity_id} alive={tgt.alive}, "
          f"{bystander.entity_id} alive={bystander.alive})")

    print("\n=== VERIFICATION ===")
    checks = {
        "both vehicles detected as distinct tracks": tgt_tid != bys_tid,
        "struck target judged DESTROYED (sensor)": verdict.get(tgt_tid) == "destroyed",
        "bystander still tracked = SURVIVED": mm.bda_status(bys_tid) == "survived",
        "verdict matches ground truth (target dead)": truth[tgt.entity_id] is False,
        "verdict matches ground truth (bystander alive)": truth[bystander.entity_id] is True,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\n=== RESULT:", "BDA PROVEN ✅" if passed else "BDA FAILED ❌", "===")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
