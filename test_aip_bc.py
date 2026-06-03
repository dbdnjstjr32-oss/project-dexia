"""Phase B+C verification — FastAPI control plane + real Ollama tactical agent.

Part B: drive the FastAPI endpoints (TestClient) and confirm each enqueues a
        validated command onto the queue.
Part C: feed a synthetic threat situation to the LLM agent and confirm it
        returns a real Course of Action (assessment + tool recommendations) by
        actually calling the local Ollama daemon.

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe test_aip_bc.py
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

import dexia.api.sim_api as sim_api
from dexia.ai.tactical_agent import TacticalAgent, summarize_situation


def part_b() -> bool:
    print("=" * 74)
    print("PART B — FastAPI simulator control plane")
    print("=" * 74)

    # Redirect the queue to a temp file so we don't disturb the live streamer.
    tmp = os.path.join(tempfile.gettempdir(), "dexia_cmd_test.json")
    if os.path.exists(tmp):
        os.remove(tmp)
    sim_api.COMMANDS_PATH = tmp

    client = TestClient(sim_api.app)
    print("\nhealth:", client.get("/api/sim/health").json()["ok"])

    calls = [
        ("POST", "/api/sim/enemy", {"x": 6.0, "y": 6.0}),
        ("POST", "/api/sim/friendly", {"x": -3.0, "y": -3.0}),
        ("POST", "/api/sim/deploy", {"x": -2.5, "y": -2.0}),
        ("POST", "/api/sim/deploy", {"x": -2.0, "y": -2.5}),
        ("POST", "/api/sim/activate", None),
    ]
    acks = []
    for _, path, body in calls:
        r = client.post(path, json=body) if body is not None else client.post(path)
        j = r.json()
        acks.append(j)
        print(f"  {path:24s} -> {r.status_code} action={j.get('action')} id={j.get('queued_id')}")

    # validation error check
    bad = client.post("/api/sim/deploy", json={"x": "not-a-number", "y": 1})
    print(f"  validation (bad x)        -> {bad.status_code} (expect 422)")

    queued = json.load(open(tmp, encoding="utf-8")) if os.path.exists(tmp) else []
    actions = [c["action"] for c in queued]
    print(f"\n  queued actions on disk: {actions}")
    ok = (
        all(a["ok"] for a in acks)
        and actions == ["set_enemy", "set_friendly", "spawn", "spawn", "arm"]
        and bad.status_code == 422
    )
    if os.path.exists(tmp):
        os.remove(tmp)
    print(f"  PART B: {'PASS' if ok else 'FAIL'}")
    return ok


def part_c() -> bool:
    print("\n" + "=" * 74)
    print("PART C — Local Ollama tactical agent (real inference)")
    print("=" * 74)

    agent = TacticalAgent()  # default qwen2.5:7b
    if not agent.available():
        print("  ollama client unavailable — skipping (install `ollama`).")
        return False

    # Synthetic threat: a friendly drone sitting INSIDE the enemy AA kill zone.
    telemetry = {
        "armed": True,
        "enemy": [5.0, 5.0, 1.0],
        "friendly": [-3.0, -3.0, 0.0],
        "network_survivability": 0.83,
        "aa": {
            "position": [5.0, 5.0, 0.0], "threat_level": "kill",
            "radar_range": 3.5, "engagement_range": 1.3, "ammo": 27, "max_ammo": 30,
            "tracked": ["agent_kami_0"],
        },
        "agents": [
            {"id": "agent_kami_0", "role": "Kamikaze 0", "pos": [4.6, 4.7, 1.4],
             "alt": 1.4, "state": "flying", "lost": False},
            {"id": "agent_kami_1", "role": "Kamikaze 1", "pos": [-2.5, -2.0, 1.5],
             "alt": 1.5, "state": "flying", "lost": False},
        ],
    }

    print("\n[상황 요약 -> LLM 입력]")
    print(summarize_situation(telemetry))

    print(f"\n[모델 {agent.model} 추론 중... 수 초 소요]")
    coa = agent.assess(telemetry)

    if not coa.get("ok"):
        print(f"  agent error: {coa.get('error')}")
        return False

    print("\n[전술 평가 (assessment)]")
    print(" ", coa["assessment"].replace("\n", "\n  "))
    print("\n[제안 행동방침 (COA / tool calls)]")
    for r in coa["recommendations"]:
        print(f"  • {r['tool']}({r['kwargs']})  -> API {TacticalAgent.to_api_call(r)['path']}")
    if not coa["recommendations"]:
        print("  (도구 호출 없음 — 모델이 텍스트 권고만 제시)")

    # A valid COA = a real assessment AND/OR concrete tool recommendations.
    ok = bool(coa["assessment"]) or bool(coa["recommendations"])
    print(f"\n  PART C: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    b = part_b()
    c = part_c()
    print("\n" + "=" * 74)
    print(f"AIP PHASE B+C: B={'PASS' if b else 'FAIL'} | C={'PASS' if c else 'FAIL'}")
    print("=" * 74)
    return 0 if (b and c) else 1


if __name__ == "__main__":
    raise SystemExit(main())
