"""OAG — Ontology-Augmented Generation context (Phase 8).

Unlike vector-RAG (blind to relational structure), OAG injects the DroneOntology's
*typed objects + explicit edges* straight into the LLM context as three layers:
mission, drone object graph, threats + links. Dexia's state is already structured,
so no vector DB is needed.
"""

from __future__ import annotations


def build_oag_context(snapshot: dict) -> str:
    objs = snapshot.get("objects", {}) or {}
    drones = objs.get("DroneObject", [])
    threats = objs.get("ThreatObject", [])
    mission = (objs.get("MissionObject") or [{}])[0]
    links = snapshot.get("links", []) or []

    out = ["[LAYER 1 — MISSION]"]
    out.append(
        f"tick={mission.get('tick')} armed={mission.get('armed')} "
        f"broadcast={mission.get('broadcast')} kill_confirmed={mission.get('kill_confirmed')} "
        f"net_surv={round(mission.get('network_survivability', 1.0), 2)} "
        f"total_lost={mission.get('total_lost', 0)}"
    )

    out.append("[LAYER 2 — DRONE OBJECT GRAPH]")
    if not drones:
        out.append("  (배치된 아군 드론 없음)")
    for d in drones:
        p = d.get("position", [0, 0, 0])
        lost = f" LOST:{d.get('loss_reason')}" if d.get("status") == "lost" else ""
        out.append(
            f"  {d['agent_id']} type={d.get('kind')} role={d.get('role')} "
            f"pos=[{p[0]:.1f},{p[1]:.1f}] alt={d.get('alt', 0):.1f} "
            f"status={d.get('status')} snr={d.get('snr_db', 0):.0f}dB "
            f"link={'OK' if d.get('link_good') else 'DEGR'}{lost}"
        )

    out.append("[LAYER 3 — THREATS & LINKS]")
    for t in threats:
        p = t.get("position", [0, 0, 0])
        out.append(
            f"  {t['aa_id']} pos=[{p[0]:.1f},{p[1]:.1f}] level={t.get('threat_level')} "
            f"kill_radius={t.get('engagement_range')} radar={t.get('radar_range')} "
            f"tracked={t.get('tracked')} ammo={t.get('ammo')}/{t.get('max_ammo')}"
        )
    kc = [l for l in links if l.get("link_type") == "KillChainLink"]
    out.append(f"  KillChainLinks: {[(l['source'], '->', l['target']) for l in kc] or '없음'}")
    return "\n".join(out)


OAG_ASSESS_SYSTEM = (
    "너는 Dexia 전장 시뮬레이터의 AI 전술 참모다. 아래는 온톨로지(타입화된 오브젝트 그래프)로 "
    "구조화된 전장 상황이다. 이 구조를 근거로 위협을 평가하고 필요한 행동을 제공된 도구(tools)로 "
    "'제안'하라. 직접 실행 권한은 없으며 모든 행동은 지휘관 승인을 거친다.\n"
    "원칙: ①적 AA 교전반경(kill zone) 안 아군은 회수(recall) 우선 ②위협 확인+아군 대기 시 적절 위치 "
    "전개/활성화 ③위협 없으면 standby. 평가(assessment)는 한국어 2~3문장."
)
