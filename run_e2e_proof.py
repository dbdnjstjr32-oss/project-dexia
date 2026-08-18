"""HONEST end-to-end proof of the AIP kill chain.

    AIP 정찰 → 탐지 → 공격계획 → (사람) 승인 → 발사 → BDA

Nothing about the *outcome* is scripted. The enemy position is hidden from the
AIP (fog of war); detection comes only from real sensor feeds, the lethal COA
must be PRODUCED BY THE REAL LLM (the commander approves the AIP's plan; a
commander-ordered fire is recorded separately and does NOT count as an AIP kill),
the round flies under the real ballistic/effect resolver, and the BDA (kill) is
read back from WorldState ground truth — it is NEVER set by this script. The only
thing we arrange is an honest, winnable engagement geometry (a legitimate
scenario design), exactly as a real exercise planner would.

Usage:
    python run_e2e_proof.py            # default 1x TB2 + 1x M777 vs procedural red
    python run_e2e_proof.py --seed 7 --max-ticks 140

Exit code 0 = chain proven AIP/LLM-driven (the LLM produced the lethal COA AND the
real round killed the targeted unit). Exit 1 = honest failure — including the case
where the target died but only because the commander ordered the fire (the LLM
produced no lethal COA). This requires a reachable Ollama model; with the model
offline the proof refuses to run rather than printing a false success.
"""

from __future__ import annotations

import argparse
import json
import sys

from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.mission_manager import MissionManager


class StaticRedCommander:
    """Enemy holds prepared/defensive positions for this proof. (The full
    maneuver/evasion AI — dexia.agent.red_commander.RedCommander — runs in the
    live sim & command_server; it is intentionally out of scope here so the
    single-round ground-truth kill is deterministic rather than chasing a
    randomly-walking target through a 40 s artillery time-of-flight.)"""

    def step(self, world, tick):
        return

AUDIT_PATH = "e2e_proof_log.jsonl"
LETHAL = ("request_fires", "engage")


def _z(world, x, y):
    t = getattr(world, "terrain", None)
    return [float(x), float(y), float(t.height(float(x), float(y)))] if t is not None \
        else [float(x), float(y)]


def setup_engagement(world):
    """Honest scenario design: keep ONE procedurally generated armor unit as the
    confirmable target and place blue assets at a real, winnable geometry. The
    rest of the random red force is set aside so the ground-truth kill assertion
    is unambiguous (the same code path handles N targets)."""
    tb2 = next((e for e in world.blue if "tb2" in e.cls or "recon" in e.cls), None)
    arty = next((e for e in world.blue if e.cls.startswith("m777") or e.cls.startswith("m142")), None)
    armor = next((e for e in world.red if e.category in ("armor", "apc")), None)
    if not (tb2 and arty and armor):
        raise SystemExit("scenario lacks TB2 / artillery / armor — cannot run proof")

    tb2.position = [0.0, 0.0, 1500.0]         # UAV on station at 1.5 km AGL (clear EO LOS over the ridge)
    arty.position = _z(world, 0, -1500)       # 4.5 km from target (M777 min 4 km, max 30 km)
    armor.position = _z(world, 0, 3000)       # the hidden enemy
    armor.behavior, armor.route, armor._motion = "static", None, None

    # set the rest of the random red force aside (kept alive but off the board)
    world.entities = [e for e in world.entities
                      if e.side == "blue" or e is armor]
    return tb2, arty, armor


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--difficulty", default="easy")
    ap.add_argument("--max-ticks", type=int, default=140)
    ap.add_argument("--model", default=None,
                    help="override the tactical model for this offline proof "
                         "(e.g. gpt-oss:20b — a stronger, latency-insensitive model "
                         "than the VRAM-resident live default qwen2.5:7b)")
    args = ap.parse_args(argv)

    if args.model:
        # The OFFLINE proof is latency-insensitive, so it may use a stronger model
        # than the live tactical default. This only changes WHICH model is asked;
        # the verification (LLM must produce the lethal COA, ground-truth kill) is
        # unchanged, so a PASS here is still an honest AIP/LLM-driven result.
        from dexia.api import llm_gateway as _gw
        _gw.ROUTING["tactical"] = ("ollama", args.model)

    print("=== HONEST AIP KILL-CHAIN PROOF ===")
    print(f"seed={args.seed} difficulty={args.difficulty} "
          f"tactical_model={args.model or 'default(qwen2.5:7b)'}\n")

    bg = BattleGenerator(seed=args.seed)
    world = bg.generate({"tb2_recon_uav": 1, "m777_howitzer": 1}, difficulty=args.difficulty)
    n_red_generated = len(world.red)
    tb2, arty, armor = setup_engagement(world)
    print(f"Procedurally generated {n_red_generated} red unit(s). "
          f"Engagement target = {armor.entity_id} ({armor.category}) — POSITION HIDDEN FROM AIP.")
    print(f"Blue: {tb2.entity_id} (ISR), {arty.entity_id} (fires)\n")

    mm = MissionManager(world, StaticRedCommander())
    if not mm.llm.available():
        print("LLM gateway unavailable (Ollama offline). Cannot run an honest live proof.")
        return 1

    audit = open(AUDIT_PATH, "w", encoding="utf-8")
    seen = 0  # how many feed entries already printed

    def drain_feed():
        nonlocal seen
        while seen < len(mm.aip_feed):
            msg = mm.aip_feed[seen]; seen += 1
            audit.write(json.dumps({"tick": mm.current_tick, **msg}) + "\n")
            print(f"T+{mm.current_tick:03d}  [{msg.get('type'):<12}] {msg.get('message')}")

    fired = False                 # have we approved + executed a lethal COA?
    fire_tick = None
    fire_source = None            # "llm_coa" (AIP produced it) | "commander_order"
    llm_prompted_on_track = False # gave the LLM an explicit decision on the track?
    detect_tick = None
    kill_tick = None

    def confident_armor():
        return next((t for t in mm.fusion.active_tracks()
                     if t.category in ("armor", "apc") and t.confidence >= 0.6), None)

    for _ in range(args.max_ticks):
        if mm.paused and mm.approval_queue:
            # ---- HUMAN-IN-THE-LOOP: the commander reviews the AIP's proposals ----
            have_track = confident_armor() is not None
            for coa in list(mm.approval_queue):
                print(f"T+{mm.current_tick:03d}  AIP recommends {coa.action} on {coa.target or '(area)'} "
                      f"by {coa.asset} (P_success {coa.expected_success:.2f}) — {coa.description}")
                if coa.action in LETHAL and not fired:
                    print(f"T+{mm.current_tick:03d}  COMMANDER: APPROVED.")
                    mm.approve_coa(coa.id)
                    fired, fire_tick, fire_source = True, mm.current_tick, "llm_coa"
                else:
                    # decline ISR detours / redundant strikes: with a confirmed
                    # track the commander commits to the engagement instead.
                    reason = "round already inbound" if fired else "track already confirmed; commit to the strike"
                    print(f"T+{mm.current_tick:03d}  COMMANDER: declined ({reason}).")
                    mm.reject_coa(coa.id)
            drain_feed()
            continue

        mm.run_cycle()
        drain_feed()

        # note the moment of first detection (real sensor truth)
        if detect_tick is None and mm.fusion.active_tracks():
            detect_tick = mm.current_tick
            t0 = mm.fusion.active_tracks()[0]
            print(f"T+{mm.current_tick:03d}  SENSOR: first track {t0.track_id} "
                  f"({t0.category}) conf {t0.confidence:.2f}")

        # Once a track is CONFIRMED, give the AIP/LLM an explicit decision point on
        # it. The planner otherwise only re-runs on a brand-NEW track, so without
        # this nudge the LLM never gets to act on a target that became confident
        # after its first (ISR) proposal — which is exactly why the old proof had
        # to close the chain with a scripted commander order and then mislabel it
        # as an AIP kill. If the LLM proposes a lethal COA it is approved above
        # (fire_source="llm_coa"). Only if the LLM STILL declines do we fall back
        # to a commander-directed fire, recorded as "commander_order" so the proof
        # never claims an AIP/LLM kill it did not make.
        if not fired and not mm.paused and mm.current_tick >= 12:
            trk = confident_armor()
            if trk is not None and not llm_prompted_on_track:
                llm_prompted_on_track = True
                print(f"T+{mm.current_tick:03d}  AIP: confirmed hostile {trk.track_id} "
                      f"(conf {trk.confidence:.2f}) — requesting a strike decision from the AIP/LLM.")
                mm._orient_and_decide()   # re-ask the live model now the target is confirmed
                drain_feed()
                continue                  # next iteration approves any lethal COA it returns
            elif trk is not None:
                print(f"T+{mm.current_tick:03d}  COMMANDER ORDER (AIP produced no lethal COA): "
                      f"engage confirmed hostile {trk.track_id} (conf {trk.confidence:.2f}) "
                      f"with {arty.entity_id}.")
                if mm.command_fires(trk.track_id, arty.entity_id):
                    fired, fire_tick, fire_source = True, mm.current_tick, "commander_order"
                drain_feed()

        # ground-truth BDA: read the kill back from the world (never set here)
        if not armor.alive and kill_tick is None:
            kill_tick = mm.current_tick

        if kill_tick is not None and not mm.paused:
            # let any final BDA feed entry flush, then stop
            mm.run_cycle(); drain_feed()
            break

    audit.close()

    # ---------------------------- VERIFICATION ---------------------------- #
    bda_events = [m for m in mm.aip_feed if m.get("type") == "BDA" and m.get("killed")]
    killed_ids = {kid for m in bda_events for kid in m.get("killed", [])}

    print("\n=== VERIFICATION (ground truth, nothing scripted) ===")
    LLM_DRIVEN = "the lethal COA was produced by the AIP/LLM (not a scripted order)"
    checks = {
        "enemy was detected by a real sensor feed": detect_tick is not None,
        LLM_DRIVEN: fire_source == "llm_coa",
        "a lethal COA was approved by the commander": fire_tick is not None,
        "target is DEAD in world ground truth": (not armor.alive),
        "the kill was the targeted unit": (armor.entity_id in killed_ids),
        "BDA reported via the resolver (not faked)": (len(bda_events) > 0),
        "chain order detect < fire < kill": (
            detect_tick is not None and fire_tick is not None and kill_tick is not None
            and detect_tick <= fire_tick < kill_tick
        ),
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    passed = all(checks.values())
    # Distinguish "the AIP/LLM closed the chain" (the claim this proof exists to
    # make) from "the chain closed at all" (a real kill, but commander-ordered).
    physical_chain = all(v for k, v in checks.items() if k != LLM_DRIVEN)
    print(f"\ntimeline: detect=T+{detect_tick}  fire=T+{fire_tick}  kill=T+{kill_tick}  "
          f"fire_source={fire_source}")
    print(f"audit log written to {AUDIT_PATH}")
    if passed:
        verdict = "CHAIN PROVEN — AIP/LLM-driven ✅"
    elif physical_chain and fire_source == "commander_order":
        verdict = ("CHAIN CLOSED but NOT AIP/LLM-driven — the kill was a commander "
                   "order, the LLM produced no lethal COA (HONEST PARTIAL) ❌")
    else:
        verdict = "CHAIN NOT CLOSED ❌"
    print("\n=== RESULT:", verdict, "===")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
