"""Phase 7 verification — OSS ontology API + k-LLM Gateway.

Part A: drive the FastAPI /ontology/* endpoints against a known snapshot.
Part B: call the k-LLM Gateway (real Ollama) and confirm routing + an audit
        record (model, token counts, latency) is written to the audit log.

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe test_phase7_oss.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from fastapi.testclient import TestClient

import dexia.api.sim_api as api
from dexia.api.llm_gateway import LLMGateway
from dexia.ontology import InMemoryRegistry, ingest


def sample_record():
    return {
        "tick": 41, "armed": True, "network_survivability": 0.83,
        "agents": [
            {"id": "agent_recon_0", "role": "Recon 0", "kind": "recon", "pos": [5, 5, 4],
             "vel": [0, 0, 0], "alt": 4.0, "speed": 0.1, "snr_db": 52, "link_good": True,
             "lost": False, "state": "flying", "equipment": "Recon Hexa"},
            {"id": "agent_kami_0", "role": "Kamikaze 0", "kind": "kami", "pos": [4.6, 4.7, 1.4],
             "vel": [0, 0, 0], "alt": 1.4, "speed": 0.2, "snr_db": 59, "link_good": True,
             "lost": False, "state": "flying", "equipment": "Tandem VTOL"},
            {"id": "agent_kami_1", "role": "Kamikaze 1", "kind": "kami", "pos": [3, 3, 1],
             "vel": [0, 0, 0], "alt": 1.0, "speed": 0.0, "snr_db": 40, "link_good": False,
             "lost": True, "loss_reason": "anti_air", "state": "flying", "equipment": "Standard Quad"},
        ],
        "aa": {"position": [5, 5, 0], "radar_range": 3.5, "engagement_range": 1.3,
               "ew_range": 4.9, "threat_level": "kill", "ammo": 27, "max_ammo": 30,
               "tracked": ["agent_kami_0"], "active_zones": [{}, {}]},
        "events": {"broadcast": True, "kill_confirmed": False, "total_lost": 1},
    }


def part_a() -> bool:
    print("=" * 72)
    print("PART A — OSS ontology query API")
    print("=" * 72)

    reg = InMemoryRegistry()
    ingest(reg, sample_record())
    tmp = os.path.join(tempfile.gettempdir(), "dexia_ont_oss_test.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg.snapshot(), f)
    api.ONTOLOGY_PATH = tmp  # point the API at the known snapshot

    c = TestClient(api.app)
    all_d = c.get("/ontology/drones").json()
    lost = c.get("/ontology/drones?status=lost").json()
    one = c.get("/ontology/drones/agent_kami_1").json()
    threats = c.get("/ontology/threats").json()
    kc = c.get("/ontology/killchain").json()
    mission = c.get("/ontology/mission").json()
    nf = c.get("/ontology/drones/nope").status_code

    print(f"  GET /ontology/drones          -> count={all_d['count']}")
    print(f"  GET /ontology/drones?status=lost -> {[d['agent_id'] for d in lost['drones']]}")
    print(f"  GET /ontology/drones/agent_kami_1 -> status={one['drone']['status']} links={len(one['links'])}")
    print(f"  GET /ontology/threats         -> count={threats['count']} level={threats['threats'][0]['threat_level']}")
    print(f"  GET /ontology/killchain       -> count={kc['count']} {[(l['source'],'->',l['target']) for l in kc['links']]}")
    print(f"  GET /ontology/mission         -> tick={mission['tick']} broadcast={mission['broadcast']}")
    print(f"  GET /ontology/drones/nope     -> {nf} (expect 404)")

    os.remove(tmp)
    ok = (all_d["count"] == 3 and len(lost["drones"]) == 1
          and one["drone"]["status"] == "lost" and len(one["links"]) >= 1
          and threats["count"] == 1 and kc["count"] == 1
          and mission["tick"] == 41 and nf == 404)
    print(f"  PART A: {'PASS' if ok else 'FAIL'}")
    return ok


def part_b() -> bool:
    print("\n" + "=" * 72)
    print("PART B — k-LLM Gateway (routing + audit)")
    print("=" * 72)

    audit = os.path.join(tempfile.gettempdir(), "dexia_llm_audit_test.jsonl")
    if os.path.exists(audit):
        os.remove(audit)
    gw = LLMGateway(audit_path=audit)
    if not gw.available():
        print("  ollama unavailable — skipping")
        return False

    print(f"  routing: tactical -> {gw._route('tactical', None)} | summary -> {gw._route('summary', None)}")
    print("  [calling local model via gateway ...]")
    g = gw.chat([{"role": "user", "content": "한 문장으로 '준비 완료'라고만 답하라."}], use_case="summary")
    print(f"  chat ok={g.get('ok')} model={g.get('model')}")

    lines = [json.loads(x) for x in open(audit, encoding="utf-8")] if os.path.exists(audit) else []
    print(f"  llm_audit.jsonl lines: {len(lines)}")
    if lines:
        a = lines[-1]
        print(f"  audit rec: provider={a['provider']} model={a['model']} "
              f"use_case={a['use_case']} latency={a['latency_ms']}ms "
              f"tokens(p/c)={a.get('prompt_tokens')}/{a.get('completion_tokens')}")
    ok = g.get("ok") and len(lines) >= 1 and lines[-1].get("latency_ms") is not None
    if os.path.exists(audit):
        os.remove(audit)
    print(f"  PART B: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    a = part_a()
    b = part_b()
    print("\n" + "=" * 72)
    print(f"PHASE 7 (OSS + k-LLM): A={'PASS' if a else 'FAIL'} | B={'PASS' if b else 'FAIL'}")
    print("=" * 72)
    return 0 if (a and b) else 1


if __name__ == "__main__":
    raise SystemExit(main())
