"""Phase 8 verification — OAG context + Logic-block pipeline.

Deterministic blocks (CommsRisk, RouteOptim, ActionBus governance) are asserted
exactly; the LLM blocks (TacticalAssess, KillChainDecision) run against the live
local Ollama and must yield a real Course of Action.

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe test_phase8_oag.py
"""

from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.ontology import InMemoryRegistry, ingest
from dexia.ai.oag import build_oag_context
from dexia.ai.blocks import CommsRiskBlock, RouteOptimBlock, avoid_route
from dexia.ai.pipeline import TacticalPipeline


def threat_record():
    return {
        "tick": 41, "armed": True, "network_survivability": 0.83,
        "agents": [
            {"id": "agent_recon_0", "role": "Recon 0", "kind": "recon", "pos": [5, 5, 4],
             "vel": [0, 0, 0], "alt": 4.0, "speed": 0.1, "snr_db": 52, "link_good": True,
             "lost": False, "state": "flying", "equipment": "Recon Hexa"},
            {"id": "agent_kami_0", "role": "Kamikaze 0", "kind": "kami", "pos": [4.6, 4.7, 1.4],
             "vel": [0, 0, 0], "alt": 1.4, "speed": 0.2, "snr_db": 59, "link_good": True,
             "lost": False, "state": "flying", "equipment": "Tandem VTOL"},
            {"id": "agent_kami_1", "role": "Kamikaze 1", "kind": "kami", "pos": [-3, -2.5, 1.5],
             "vel": [0, 0, 0], "alt": 1.5, "speed": 0.0, "snr_db": 40, "link_good": False,
             "lost": False, "state": "flying", "equipment": "Standard Quad"},
        ],
        "aa": {"position": [5, 5, 0], "radar_range": 3.5, "engagement_range": 1.3,
               "ew_range": 4.9, "threat_level": "kill", "ammo": 27, "max_ammo": 30,
               "tracked": ["agent_kami_0"], "active_zones": [{}, {}]},
        "events": {"broadcast": True, "kill_confirmed": False, "total_lost": 0},
    }


def main() -> int:
    print("=" * 74)
    print("PHASE 8 — OAG + Logic-block pipeline")
    print("=" * 74)

    reg = InMemoryRegistry()
    ingest(reg, threat_record())
    snap = reg.snapshot()

    # ---- OAG context ----
    oag = build_oag_context(snap)
    print("\n[OAG 컨텍스트]\n  " + oag.replace("\n", "\n  "))
    oag_ok = ("LAYER 1" in oag and "LAYER 2" in oag and "LAYER 3" in oag
              and "KillChainLinks" in oag)

    # ---- deterministic blocks ----
    ctx = CommsRiskBlock().run({"snapshot": snap})
    comms = ctx["comms"]
    print(f"\n[CommsRiskBlock] net_surv={comms['network_survivability']} "
          f"degraded={comms['degraded']} min_snr={comms['min_snr']}")
    comms_ok = (abs(comms["network_survivability"] - 2 / 3) < 0.01
                and comms["degraded"] == ["agent_kami_1"])

    r_near = avoid_route([0, 0], [5, 5], [5, 5, 0], 1.3)   # passes the AA -> detour
    r_far = avoid_route([0, 0], [-4, -4], [5, 5, 0], 1.3)  # clear -> straight
    print(f"[RouteOptimBlock] deploy→[5,5] near AA: {len(r_near)} pts (detour) | "
          f"deploy→[-4,-4] clear: {len(r_far)} pts")
    route_ok = len(r_near) == 3 and len(r_far) == 2

    # ---- full pipeline (LLM blocks via live Ollama) ----
    print("\n[전체 파이프라인 실행 (LLM 블록 = 실 Ollama, 수 초)...]")
    coa = TacticalPipeline().run(snap)
    print(f"  blocks: {' → '.join(coa['blocks'])}")
    print(f"  model: {coa.get('model')}")
    print(f"  assessment[:160]: {(coa.get('assessment') or '')[:160]}")
    print(f"  recommendations:")
    for r in coa.get("recommendations", []):
        print(f"    • {r.get('tool')}({r.get('kwargs')}) governance={r.get('governance')}"
              f"{' route='+str(len(r['route']))+'pts' if r.get('route') else ''}")
    if coa.get("killchain_decision"):
        print(f"  killchain_decision: {coa['killchain_decision'][:120]}")

    pipe_ok = (coa["ok"] and len(coa["blocks"]) == 5
               and (coa.get("assessment") or coa.get("recommendations")))

    ok = oag_ok and comms_ok and route_ok and pipe_ok
    print("\n" + "=" * 74)
    print(f"PHASE 8: OAG={'P' if oag_ok else 'F'} CommsRisk={'P' if comms_ok else 'F'} "
          f"Route={'P' if route_ok else 'F'} Pipeline={'P' if pipe_ok else 'F'} "
          f"-> {'PASS' if ok else 'FAIL'}")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
