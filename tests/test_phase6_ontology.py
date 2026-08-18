"""Phase 6.1 verification — DroneOntology data transformation.

Proves the flat-telemetry → Semantic-Object pipeline without touching the physics
engine: a dummy flat telemetry dict is parsed into typed DroneObject/ThreatObject
instances, loaded into an OntologyRegistry, and queried for the drones caught
inside a threat's kill radius.

Dual-mode: ``pytest tests/test_phase6_ontology.py`` *and* direct
(``python tests/test_phase6_ontology.py``, which prints the transformation).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.ontology import (
    DroneObject,
    OntologyRegistry,
    Position,
    ThreatObject,
    parse_telemetry_to_ontology,
)

# A dummy flat telemetry dict (the shape the streamer / telemetry.json emits).
# AA threat at [5,5,0] with a 2.0 m kill radius:
#   kami_0 [5,5,1]  -> 1.00 m  IN range (active)
#   kami_2 [6,5,1]  -> 1.41 m  IN range but LOST
#   kami_1 [20,20,1]-> ~21 m   out of range
#   recon_0 [0,0,3] -> ~7.7 m  out of range
DUMMY_TELEMETRY = {
    "tick": 100,
    "agents": [
        {"id": "agent_recon_0", "role": "Recon 0", "kind": "recon", "pos": [0, 0, 3],
         "lost": False, "state": "flying"},
        {"id": "agent_kami_0", "role": "Kamikaze 0", "kind": "kami", "pos": [5, 5, 1],
         "lost": False, "state": "flying"},
        {"id": "agent_kami_1", "role": "Kamikaze 1", "kind": "kami", "pos": [20, 20, 1],
         "lost": False, "state": "flying"},
        {"id": "agent_kami_2", "role": "Kamikaze 2", "kind": "kami", "pos": [6, 5, 1],
         "lost": True, "state": "lost", "loss_reason": "aa_hit"},
    ],
    "aa": {"position": [5, 5, 0], "radar_range": 3.5, "engagement_range": 1.5,
           "kill_radius": 2.0, "ammo": 24, "max_ammo": 24, "threat_level": "kill"},
}


def _registry() -> OntologyRegistry:
    objects = parse_telemetry_to_ontology(DUMMY_TELEMETRY)
    return OntologyRegistry().load(objects)


# --------------------------------------------------------------------------- #
def test_serializer_produces_typed_objects():
    objs = parse_telemetry_to_ontology(DUMMY_TELEMETRY)
    drones = [o for o in objs if isinstance(o, DroneObject)]
    threats = [o for o in objs if isinstance(o, ThreatObject)]
    assert len(drones) == 4 and len(threats) == 1
    aa = threats[0]
    assert aa.aa_id == "aa_0" and aa.kill_radius == 2.0
    assert isinstance(aa.xyz(), Position) and aa.xyz().to_list() == [5.0, 5.0, 0.0]
    # status mapping: the lost kami is not active
    k2 = next(d for d in drones if d.agent_id == "agent_kami_2")
    assert k2.status == "lost" and k2.active is False


def test_registry_active_drones():
    reg = _registry()
    active = {d.agent_id for d in reg.get_active_drones()}
    assert active == {"agent_recon_0", "agent_kami_0", "agent_kami_1"}  # kami_2 lost


def test_threats_in_range_query():
    reg = _registry()
    # a friendly waypoint near the AA finds the threat; a far one does not
    assert len(reg.get_threats_in_range(Position(4, 4, 0), 3.0)) == 1
    assert len(reg.get_threats_in_range(Position(30, 30, 0), 3.0)) == 0


def test_drones_within_threat_kill_radius():
    """The headline query: which drones are inside the AA kill radius?"""
    reg = _registry()
    aa = reg.all_threats()[0]
    # all drones geometrically inside the 2.0 m kill radius (incl. the lost one)
    inside = {d.agent_id for d in reg.get_drones_in_range(aa.xyz(), aa.kill_radius)}
    assert inside == {"agent_kami_0", "agent_kami_2"}
    # endangered = active drones the threat can still kill
    endangered = {d.agent_id for d in reg.get_drones_threatened_by(aa)}
    assert endangered == {"agent_kami_0"}


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA Phase 6.1 — DroneOntology: flat telemetry → Semantic Objects")
    print(bar)

    objs = parse_telemetry_to_ontology(DUMMY_TELEMETRY)
    print(f"\n[1] parse_telemetry_to_ontology → {len(objs)} typed objects")
    for o in objs:
        kind = type(o).__name__
        oid = getattr(o, "agent_id", None) or getattr(o, "aa_id", "")
        extra = f"status={o.status}" if isinstance(o, DroneObject) else f"kill_radius={o.kill_radius}"
        print(f"      {kind:<12} {oid:<14} pos={o.xyz().to_list()}  {extra}")

    reg = OntologyRegistry().load(objs)
    aa = reg.all_threats()[0]

    print(f"\n[2] OntologyRegistry queries")
    print(f"      get_active_drones()           → "
          f"{[d.agent_id for d in reg.get_active_drones()]}")
    print(f"      get_threats_in_range([4,4,0],3) → "
          f"{[t.aa_id for t in reg.get_threats_in_range(Position(4, 4, 0), 3.0)]}")

    print(f"\n[3] Threat '{aa.aa_id}' @ {aa.xyz().to_list()} kill_radius={aa.kill_radius} m")
    inside = reg.get_drones_in_range(aa.xyz(), aa.kill_radius)
    for d in inside:
        dist = round(d.xyz().distance_to(aa.xyz()), 2)
        flag = "ACTIVE — ENDANGERED" if d.active else "already lost"
        print(f"      {d.agent_id:<14} dist={dist} m ≤ {aa.kill_radius}  ({flag})")
    endangered = reg.get_drones_threatened_by(aa)

    ok = ({d.agent_id for d in inside} == {"agent_kami_0", "agent_kami_2"}
          and {d.agent_id for d in endangered} == {"agent_kami_0"})
    print("\n" + bar)
    print(f"ONTOLOGY TRANSFORM {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"(2 drones in kill radius; 1 active/endangered: "
          f"{[d.agent_id for d in endangered]})")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
