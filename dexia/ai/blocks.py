"""AIP Logic Blocks (Phase 8) — composable tactical-decision pipeline.

Mirrors Palantir AIP Logic's block-chain pattern: chain `Use LLM` blocks with
deterministic `Execute Function` blocks and an `Apply Action` block. Each block
reads/writes a shared context dict.

    CommsRiskBlock        Execute Function   SNR graph -> network survivability (NumPy)
    TacticalAssessBlock   Use LLM (temp=0)   OAG context -> assessment + tool recs
    RouteOptimBlock       Execute Function   deploy target + AA zone -> avoiding route (NumPy)
    KillChainDecisionBlock Use LLM (temp=0)  (if broadcast) strike entry recommendation
    MissionUpdateBlock    Apply Action       recs -> ActionBus governance (MAC + audit)
"""

from __future__ import annotations

import json

import numpy as np

from ..api.llm_gateway import get_gateway
from ..ontology import ActionBus
from ..ontology.serializer import registry_from_snapshot
from .oag import OAG_ASSESS_SYSTEM
from .tactical_agent import TOOLS, _extract_tools_from_text


class Block:
    name = "Block"

    def run(self, ctx: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Execute Function blocks (deterministic)
# --------------------------------------------------------------------------- #
class CommsRiskBlock(Block):
    name = "CommsRiskBlock"

    def run(self, ctx: dict) -> dict:
        drones = [d for d in ctx["snapshot"]["objects"].get("DroneObject", [])
                  if d.get("status") != "lost"]
        if not drones:
            ctx["comms"] = {"network_survivability": 1.0, "degraded": [], "min_snr": None}
            return ctx
        good = np.array([1.0 if d.get("link_good") else 0.0 for d in drones])
        snr = np.array([float(d.get("snr_db", 0.0)) for d in drones])
        ctx["comms"] = {
            "network_survivability": round(float(good.mean()), 3),
            "degraded": [d["agent_id"] for d in drones if not d.get("link_good")],
            "min_snr": round(float(snr.min()), 1),
        }
        return ctx


def _point_segment_dist(p, a, b) -> float:
    p, a, b = (np.array(x, dtype=float) for x in (p, a, b))
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-9), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def avoid_route(start, goal, aa_pos, kill_r, margin: float = 0.8) -> list:
    """Straight line start->goal, with a perpendicular detour waypoint if the
    segment passes within (kill_r + margin) of the AA — keeps the route out of
    the lethal engagement zone."""
    s = np.array(start[:2], float)
    g = np.array(goal[:2], float)
    a = np.array(aa_pos[:2], float)
    R = float(kill_r) + margin
    if _point_segment_dist(a, s, g) >= R:
        return [s.tolist(), g.tolist()]
    seg = g - s
    L = float(np.linalg.norm(seg))
    u = seg / L if L > 1e-6 else np.array([1.0, 0.0])
    proj = s + u * float(np.dot(a - s, u))
    side = a - proj
    n = float(np.linalg.norm(side))
    away = (-side / n) if n > 1e-6 else np.array([-u[1], u[0]])
    wp = proj + away * R * 1.2
    return [s.tolist(), wp.tolist(), g.tolist()]


class RouteOptimBlock(Block):
    name = "RouteOptimBlock"

    def run(self, ctx: dict) -> dict:
        threats = ctx["snapshot"]["objects"].get("ThreatObject", [])
        aa = threats[0] if threats else None
        base = ctx.get("friendly_base", [0.0, 0.0])
        for rec in ctx.get("recommendations", []):
            if rec.get("tool") == "deploy_drone" and aa:
                k = rec.get("kwargs", {})
                goal = [k.get("x", 0.0), k.get("y", 0.0)]
                rec["route"] = avoid_route(base, goal, aa["position"], aa.get("engagement_range", 0.0))
        return ctx


# --------------------------------------------------------------------------- #
# Use LLM blocks (via k-LLM Gateway, temperature 0 = deterministic tactics)
# --------------------------------------------------------------------------- #
def _parse_message(resp):
    msg = resp.get("message", {}) if isinstance(resp, dict) else getattr(resp, "message", {})
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
    tcs = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
    recs = []
    for tc in (tcs or []):
        fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", {})
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        recs.append({"tool": name, "kwargs": args or {}})
    return (content or "").strip(), recs


class TacticalAssessBlock(Block):
    name = "TacticalAssessBlock"

    def __init__(self, gateway=None):
        self.gw = gateway or get_gateway()

    def run(self, ctx: dict) -> dict:
        messages = [
            {"role": "system", "content": OAG_ASSESS_SYSTEM},
            {"role": "user", "content": f"[온톨로지 컨텍스트(OAG)]\n{ctx['oag_context']}\n\n"
                                        f"위 구조화된 상황을 평가하고 필요한 행동을 도구로 제안하라."},
        ]
        g = self.gw.chat(messages, use_case="tactical", tools=TOOLS, temperature=0.0)
        if not g.get("ok"):
            ctx.setdefault("assessment", "")
            ctx.setdefault("recommendations", [])
            return ctx
        ctx["model"] = g.get("model")
        content, recs = _parse_message(g["response"])
        if not recs:
            recs = _extract_tools_from_text(content)
        # tool-calling models often return empty text -> ensure the HITL card
        # always has a Korean rationale (tools-free follow-up via the gateway).
        if not content:
            acts = ", ".join(f"{r['tool']}({r['kwargs']})" for r in recs) or "행동 없음(현상 유지)"
            f = self.gw.chat(
                [
                    {"role": "system", "content": "너는 Dexia 전술 참모다. 상황과 결정된 행동을 한국어 2~3문장으로 간결히 설명·정당화하라."},
                    {"role": "user", "content": f"[상황]\n{ctx['oag_context']}\n\n[결정된 행동]\n{acts}\n\n왜 적절한지 평가하라."},
                ],
                use_case="summary", temperature=0.3,
            )
            if f.get("ok"):
                content, _ = _parse_message(f["response"])
        ctx["assessment"] = content
        ctx["recommendations"] = recs
        return ctx


class KillChainDecisionBlock(Block):
    name = "KillChainDecisionBlock"

    def __init__(self, gateway=None):
        self.gw = gateway or get_gateway()

    def run(self, ctx: dict) -> dict:
        mission = (ctx["snapshot"]["objects"].get("MissionObject") or [{}])[0]
        if not mission.get("broadcast"):
            return ctx  # no strike decision before the kill-chain broadcast
        g = self.gw.chat(
            [
                {"role": "system", "content": "너는 타격 결심 참모다. 방송된 표적에 대한 자폭 드론 진입 경로/타이밍을 한국어 1~2문장으로 권고하라."},
                {"role": "user", "content": ctx["oag_context"]},
            ],
            use_case="tactical", temperature=0.0,
        )
        if g.get("ok"):
            content, _ = _parse_message(g["response"])
            ctx["killchain_decision"] = content
        return ctx


# --------------------------------------------------------------------------- #
# Apply Action block (ActionBus governance — MAC + audit, no auto-execute)
# --------------------------------------------------------------------------- #
class MissionUpdateBlock(Block):
    name = "MissionUpdateBlock"

    _ACTION_MAP = {
        "deploy_drone": ("deploy", lambda k: {"x": k.get("x"), "y": k.get("y")}),
        "recall_drone": ("recall", lambda k: {}),
        "activate": ("activate", lambda k: {}),
        "standby": ("standby", lambda k: {}),
        "engage": ("engage", lambda k: {}),
    }

    def run(self, ctx: dict) -> dict:
        registry = registry_from_snapshot(ctx["snapshot"])
        bus = ActionBus(registry)
        for rec in ctx.get("recommendations", []):
            tool = rec.get("tool")
            kw = rec.get("kwargs", {})
            if tool in self._ACTION_MAP:
                action_type, mk = self._ACTION_MAP[tool]
                res = bus.submit(action_type, agent_id=kw.get("agent_id"), payload=mk(kw))
                rec["governance"] = res["status"]
                if res["status"] == "rejected":
                    rec["governance_reason"] = res.get("reason")
        return ctx
