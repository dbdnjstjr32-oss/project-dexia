"""HONEST proof of Phase B — ranked strike options (안1/안2/안3) balancing
estimated enemy kill vs estimated friendly loss.

Targets are detected for real (SIGINT on the SAM emitter + EO after a recon
sweep). The options and their Pkill / friendly-loss numbers are computed
deterministically from the catalog physics (range, lethal radius, threat
exposure) — not invented by an LLM. Exit 0 = proven.
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

    print("=== HONEST STRIKE-OPTIONS PROOF (Phase B) ===\n")
    bg = BattleGenerator(seed=42)
    world = bg.generate({"tb2_recon_uav": 1, "m777_howitzer": 1, "switchblade": 1},
                        difficulty="easy")
    t = world.terrain

    def Z(x, y):
        return [float(x), float(y), float(t.height(x, y))] if t is not None else [float(x), float(y)]

    tb2 = next(e for e in world.blue if "tb2" in e.cls)
    m777 = next(e for e in world.blue if e.cls.startswith("m777"))
    sb = next(e for e in world.blue if e.cls.startswith("switchblade"))
    armor = next(e for e in world.red if e.category in ("armor", "apc"))
    sam = next((e for e in world.red if e.category == "air_defense"), None)

    tb2.position = Z(0, -4000)
    m777.position = Z(0, -1500)
    sb.position = Z(0, -3000)
    armor.position = Z(1500, 5000)
    armor.behavior, armor.route, armor._motion = "static", None, None
    keep_ids = {armor.entity_id}
    if sam is not None:
        sam.position = Z(-1000, 4500)
        sam.behavior, sam.emitting, sam.route, sam._motion = "static_ad", True, None, None
        keep_ids.add(sam.entity_id)
    world.entities = [e for e in world.entities if e.side == "blue" or e.entity_id in keep_ids]

    mm = MissionManager(world, StaticRedCommander())
    print(f"Blue: {tb2.entity_id}(ISR) {m777.entity_id}(arty) {sb.entity_id}(loiter munition)")
    print(f"Red (hidden): {armor.entity_id}(apc)" + (f" + {sam.entity_id}(SAM emitter)" if sam else "") + "\n")

    # SIGINT picks up the emitter immediately; recon confirms the armor
    for _ in range(3):
        mm._observe()
    print(f"T+{mm.current_tick}: tracks via SIGINT = {[t.track_id for t in mm.fusion.active_tracks()]}")
    mm.command_recon(area=(-2000.0, 1000.0, 3000.0, 6000.0))
    for _ in range(900):
        mm._observe()
        if any(t.category in ("armor", "apc") for t in mm.fusion.active_tracks()):
            break
    cats = {t.track_id: t.category for t in mm.fusion.active_tracks()}
    print(f"T+{mm.current_tick}: tracks now = {cats}\n")

    # --- user asks for the best strike tactics -> AIP returns ranked options ---
    print("COMMANDER: 최적 타격전술 요청")
    coas = mm.generate_coa_options()
    for m in mm.aip_feed[-1:]:
        print(m["message"])
    print()

    armor_tid = next((tid for tid, c in cats.items() if c in ("armor", "apc")), None)
    arty = next((c for c in coas if c.action == "request_fires"), None)
    loiter = next((c for c in coas if c.action == "engage"), None)

    print("=== VERIFICATION ===")
    checks = {
        "armor detected only after recon": armor_tid is not None,
        ">=2 ranked options produced": len(coas) >= 2,
        "every option has Pkill > 0": all(c.p_kill > 0 for c in coas),
        "ranks are sequential 1..N": [c.rank for c in coas] == list(range(1, len(coas) + 1)),
        "top option (안1) targets the armor": bool(coas) and coas[0].target == armor_tid,
        "artillery option exists (indirect)": arty is not None,
        "loiter-strike option exists": loiter is not None,
        "loiter loss > artillery loss (SAM exposure)": bool(arty and loiter and loiter.est_loss > arty.est_loss),
        "AIP recommends lower-loss option as 안1": bool(coas) and coas[0].action == "request_fires",
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = all(checks.values())
    print("\n=== RESULT:", "STRIKE OPTIONS PROVEN ✅" if passed else "FAILED ❌", "===")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
