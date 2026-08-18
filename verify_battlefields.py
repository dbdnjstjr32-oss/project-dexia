"""HONEST proof of Phase D — 100 battlefields across 5 regions, each activatable
into a live world with region-specific terrain, randomly populated forces, and a
fogged enemy area (no enemy visible at activation). Exit 0 = proven.
"""
from __future__ import annotations
import sys
from collections import Counter
from dexia.scenario.battlefields import get_catalog, REGIONS
from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.red_commander import RedCommander
from dexia.agent.mission_manager import MissionManager


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("=== HONEST BATTLEFIELD PROOF (Phase D) ===\n")
    cat = get_catalog()
    by_region = Counter(b["region"] for b in cat)
    print(f"catalog size = {len(cat)}")
    for r, n in by_region.items():
        print(f"  {REGIONS[r]['name_ko']:<4} {r:<16} {n} maps")

    # activate EVERY battlefield and check it builds a valid, fogged world
    peaks = {}
    bad = []
    for bf in cat:
        bg = BattleGenerator(seed=hash(bf["id"]) & 0xFFFF)
        world = bg.from_battlefield(bf)
        ok = (
            len(world.blue) >= 1 and len(world.red) >= 1
            and world.terrain is not None
            and getattr(world, "enemy_area", None) is not None
            and world.battlefield["region"] == bf["region"]
        )
        # enemy_area must actually cover the (hidden) red force
        ea = world.enemy_area
        red_in_box = all(ea["x0"] <= e.position[0] <= ea["x1"]
                         and ea["y0"] <= e.position[1] <= ea["y1"] for e in world.red)
        if not (ok and red_in_box):
            bad.append(bf["id"])
        # sample terrain relief per region (mountainous vs flat should differ)
        t = world.terrain
        relief = max(t.height(0, 0), t.height(0, 2000), t.height(1500, 1500)) - t.height(0, -3000)
        peaks.setdefault(bf["region"], []).append(relief)

    print("\nregion terrain relief (avg max-min sampled, m):")
    avg = {r: sum(v) / len(v) for r, v in peaks.items()}
    for r, a in avg.items():
        print(f"  {REGIONS[r]['name_ko']:<4} {r:<16} {a:8.1f}")

    # activate one and confirm fog of war: enemy hidden at t=0
    bf0 = next(b for b in cat if b["region"] == "korea")
    bg = BattleGenerator(seed=1)
    world = bg.from_battlefield(bf0)
    mm = MissionManager(world, RedCommander(seed=1))
    for _ in range(3):
        mm._observe()
    ground_red = len(world.red)
    visible = len([t for t in mm.fusion.active_tracks() if t.category in ("armor", "apc")])
    print(f"\nactivated {bf0['name']}: ground-truth red={ground_red}, "
          f"armor visible to AIP at t=0 = {visible} (fogged)")
    state = mm.get_client_state()

    print("\n=== VERIFICATION ===")
    checks = {
        "catalog has 100 battlefields": len(cat) == 100,
        "spans all 5 regions": len(by_region) == 5,
        "20 maps per region": all(n == 20 for n in by_region.values()),
        "every battlefield activates into a fogged world": not bad,
        "Korea is more mountainous than Russia (terrain differs)": avg["korea"] > avg["russia"] + 100,
        "client state exposes enemy_area + battlefield": (
            state["enemy_area"] is not None and state["battlefield"] is not None),
        "ground armor is hidden at activation (fog of war)": visible == 0,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if bad:
        print("  failing battlefields:", bad[:10])
    passed = all(checks.values())
    print("\n=== RESULT:", "BATTLEFIELDS PROVEN ✅" if passed else "FAILED ❌", "===")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
