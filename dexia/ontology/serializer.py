"""Serializer — DroneState / telemetry record -> ontology objects + links.

Runs *after* the physics step (on the telemetry record the streamer already
builds), so the simulation hot loop and ``DroneState`` / ``PhysicsEngine`` ABC
stay untouched (per the AIP plan's invariant boundary + serialization-overhead
risk mitigation).
"""

from __future__ import annotations

from .schema import (
    CommsLink,
    DroneObject,
    KillChainLink,
    MissionObject,
    ThreatObject,
)


def objects_from_telemetry(record: dict) -> dict:
    """Convert one telemetry record into typed ontology objects + links."""
    drones: list[DroneObject] = []
    links: list = []

    for a in record.get("agents", []):
        status = "lost" if a.get("lost") else a.get("state", "active")
        drones.append(
            DroneObject(
                agent_id=a["id"],
                role=a.get("role", ""),
                kind=a.get("kind", "kami"),
                position=list(a.get("pos", [0.0, 0.0, 0.0])),
                velocity=list(a.get("vel", [0.0, 0.0, 0.0])),
                alt=float(a.get("alt", 0.0)),
                speed=float(a.get("speed", 0.0)),
                snr_db=float(a.get("snr_db", 0.0)),
                link_good=bool(a.get("link_good", True)),
                status=status,
                loss_reason=a.get("loss_reason"),
                equipment=a.get("equipment", "Standard Quad"),
            )
        )
        links.append(
            CommsLink(
                drone_id=a["id"],
                snr_db=float(a.get("snr_db", 0.0)),
                packet_lost=not bool(a.get("link_good", True)),
                state="GOOD" if a.get("link_good", True) else "BAD",
            )
        )

    threats: list[ThreatObject] = []
    aa = record.get("aa")
    if aa:
        threats.append(
            ThreatObject(
                aa_id="aa_0",
                position=list(aa.get("position", [0.0, 0.0, 0.0])),
                radar_range=float(aa.get("radar_range", 0.0)),
                engagement_range=float(aa.get("engagement_range", 0.0)),
                ew_range=float(aa.get("ew_range", 0.0)),
                threat_level=aa.get("threat_level", "idle"),
                ammo=int(aa.get("ammo", 0)),
                max_ammo=int(aa.get("max_ammo", 0)),
                tracked=list(aa.get("tracked", [])),
                active_zones=len(aa.get("active_zones", [])),
            )
        )

    ev = record.get("events", {}) or {}
    mission = MissionObject(
        mission_id="mission_0",
        tick=int(record.get("tick", 0)),
        armed=bool(record.get("armed", False)),
        broadcast=bool(ev.get("broadcast", False)),
        kill_confirmed=bool(ev.get("kill_confirmed", False)),
        network_survivability=float(record.get("network_survivability", 1.0)),
        total_lost=int(ev.get("total_lost", 0)),
    )

    # explicit kill-chain edges once a recon has broadcast the target
    if ev.get("broadcast"):
        for d in drones:
            if d.kind == "recon":
                links.append(
                    KillChainLink(
                        source=d.agent_id,
                        target="enemy_target",
                        confirmed_at_tick=mission.tick if ev.get("kill_confirmed") else None,
                    )
                )

    return {"drones": drones, "threats": threats, "mission": mission, "links": links}


def ingest(registry, record: dict) -> dict:
    """Serialize a telemetry record and upsert it into the registry."""
    o = objects_from_telemetry(record)
    registry.replace("DroneObject", o["drones"])
    registry.replace("ThreatObject", o["threats"])
    registry.replace("MissionObject", [o["mission"]])
    registry.set_links(o["links"])
    return o
