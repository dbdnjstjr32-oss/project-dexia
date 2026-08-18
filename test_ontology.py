"""Phase 6 verification — DroneOntology semantic layer.

Verifies: telemetry record -> typed object graph (registry), queries, and the
ActionBus governance (schema + state + MAC kill-chain validation + audit log).
Pure stdlib ontology -> runs on the system Python 3.13.

Run:
    python test_ontology.py
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

from dexia.ontology import (
    ActionBus,
    InMemoryRegistry,
    ingest,
)


def sample_record(broadcast=True):
    return {
        "tick": 41,
        "armed": True,
        "network_survivability": 0.83,
        "agents": [
            {"id": "agent_recon_0", "role": "Recon 0", "kind": "recon",
             "pos": [5.0, 5.0, 4.0], "vel": [0, 0, 0], "alt": 4.0, "speed": 0.1,
             "snr_db": 52, "link_good": True, "lost": False, "state": "flying",
             "equipment": "Recon Hexa"},
            {"id": "agent_kami_0", "role": "Kamikaze 0", "kind": "kami",
             "pos": [4.6, 4.7, 1.4], "vel": [0, 0, 0], "alt": 1.4, "speed": 0.2,
             "snr_db": 59, "link_good": True, "lost": False, "state": "flying",
             "equipment": "Tandem VTOL"},
            {"id": "agent_kami_1", "role": "Kamikaze 1", "kind": "kami",
             "pos": [3.0, 3.0, 1.0], "vel": [0, 0, 0], "alt": 1.0, "speed": 0.0,
             "snr_db": 40, "link_good": False, "lost": True, "loss_reason": "anti_air",
             "state": "flying", "equipment": "Standard Quad"},
        ],
        "aa": {"position": [5.0, 5.0, 0.0], "radar_range": 3.5, "engagement_range": 1.3,
               "ew_range": 4.9, "threat_level": "kill", "ammo": 27, "max_ammo": 30,
               "tracked": ["agent_kami_0"], "active_zones": [{}, {}]},
        "events": {"broadcast": broadcast, "kill_confirmed": False, "total_lost": 1},
    }


def main() -> int:
    print("=" * 72)
    print("PHASE 6 — DroneOntology (semantic layer) verification")
    print("=" * 72)

    reg = InMemoryRegistry()
    ingest(reg, sample_record(broadcast=True))

    nd = reg.count("DroneObject")
    nt = reg.count("ThreatObject")
    nm = reg.count("MissionObject")
    kc = reg.links_of("KillChainLink")
    cl = reg.links_of("CommsLink")
    lost = reg.query("DroneObject", status="lost")
    print("\n[object graph]")
    print(f"  DroneObject={nd}  ThreatObject={nt}  MissionObject={nm}")
    print(f"  links: CommsLink={len(cl)}  KillChainLink={len(kc)}")
    print(f"  query status=lost -> {[d.agent_id for d in lost]}")
    print(f"  KillChainLink: {[(l.source, '->', l.target) for l in kc]}")

    graph_ok = (nd == 3 and nt == 1 and nm == 1 and len(cl) == 3
                and len(kc) == 1 and len(lost) == 1 and lost[0].agent_id == "agent_kami_1")

    # snapshot must be JSON-serializable (OAG context payload)
    snap = reg.snapshot()
    json.dumps(snap)
    print(f"  snapshot JSON-serializable: True ({sum(len(v) for v in snap['objects'].values())} objects)")

    # ---- ActionBus governance ----
    audit = os.path.join(tempfile.gettempdir(), "dexia_action_audit_test.jsonl")
    if os.path.exists(audit):
        os.remove(audit)
    bus = ActionBus(reg, audit_path=audit, bypass=False)

    r_ok = bus.submit("move", "agent_kami_0", {"x": 1.0, "y": 2.0})          # alive + props
    r_lost = bus.submit("move", "agent_kami_1", {"x": 1.0, "y": 2.0})        # LOST -> deny
    r_missing = bus.submit("move", "agent_kami_0", {"x": 1.0})               # missing y -> deny
    r_eng_ok = bus.submit("engage", "agent_kami_0")                          # broadcast=True -> allow

    # MAC: kami engage BEFORE broadcast -> deny (re-ingest pre-broadcast state)
    ingest(reg, sample_record(broadcast=False))
    r_eng_deny = bus.submit("engage", "agent_kami_0")

    # bypass mode (training) -> always accepted, logged only
    bus_bypass = ActionBus(reg, audit_path=audit, bypass=True)
    r_bypass = bus_bypass.submit("engage", "agent_kami_1")

    print("\n[ActionBus governance]")
    print(f"  move alive+props        -> {r_ok['status']}")
    print(f"  move LOST drone         -> {r_lost['status']} ({r_lost.get('reason')})")
    print(f"  move missing y          -> {r_missing['status']} ({r_missing.get('reason')})")
    print(f"  engage after broadcast  -> {r_eng_ok['status']}")
    print(f"  engage BEFORE broadcast -> {r_eng_deny['status']} ({r_eng_deny.get('reason')})")
    print(f"  bypass mode (training)  -> {r_bypass['status']} (mode={r_bypass['mode']})")

    audit_lines = sum(1 for _ in open(audit, encoding="utf-8")) if os.path.exists(audit) else 0
    print(f"  audit log lines written : {audit_lines}")

    bus_ok = (
        r_ok["status"] == "accepted"
        and r_lost["status"] == "rejected"
        and r_missing["status"] == "rejected"
        and r_eng_ok["status"] == "accepted"
        and r_eng_deny["status"] == "rejected"
        and r_bypass["status"] == "accepted"
        and audit_lines == 6
    )
    if os.path.exists(audit):
        os.remove(audit)

    ok = graph_ok and bus_ok
    print("\n" + "=" * 72)
    print(f"PHASE 6 ONTOLOGY: graph={'PASS' if graph_ok else 'FAIL'} | "
          f"actionbus={'PASS' if bus_ok else 'FAIL'} -> {'PASS' if ok else 'FAIL'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
