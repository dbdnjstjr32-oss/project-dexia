"""Local LLM tactical agent (AI Staff) — Phase C of the AIP stack.

Replaces the frontend ``mockRag.js`` if/else rules with a *real* local model
(Ollama) given function-calling tools that mirror the Dexia control API. The
agent reads the live battlefield telemetry, assesses the threat, and proposes a
Course of Action (COA) as structured tool calls — it NEVER auto-executes
(execution is gated by the Phase-D human-in-the-loop approval).

Air-gapped by design: inference runs on the local Ollama daemon (no internet).
Defaults to ``qwen2.5:7b`` (strong tool-use + Korean); swappable to
``llama3.1:8b`` etc. Structured so it can later be wrapped by LangChain/LlamaIndex
without changing the tool contract.
"""

from __future__ import annotations

import json
from typing import Any

from ..api.llm_gateway import LLMGateway, get_gateway
from ..ontology.actions import TOOL_TO_ACTION, ollama_tools
from ..ontology.actions import to_api_call as _registry_to_api_call

DEFAULT_MODEL = "qwen2.5:7b"  # consolidated live model (strong Korean + native tool use); see llm_gateway.ROUTING

_KNOWN_TOOLS = set(TOOL_TO_ACTION)  # derived from the ActionType registry


def _extract_tools_from_text(text: str) -> list[dict]:
    """Fallback: pull tool calls a model emitted as TEXT JSON (common with small
    local models) instead of structured tool_calls.

    [결함 24] The previous regex r'\\{(?:[^{}]|\\{[^{}]*\\})*\\}' only handled
    one level of brace nesting, causing it to silently discard tool calls whose
    argument objects contained nested JSON (e.g. waypoint arrays, sub-dicts).

    Replacement: linear scan with json.JSONDecoder.raw_decode() which correctly
    handles arbitrary nesting depth and returns the exact consumed character
    count so we advance without re-scanning already-processed text.
    """
    recs = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text or ''):
        try:
            obj, end_idx = decoder.raw_decode(text, idx)
            if isinstance(obj, dict) and obj.get('name') in _KNOWN_TOOLS:
                args = obj.get('arguments') or obj.get('parameters') or {}
                recs.append({'tool': obj['name'], 'kwargs': args if isinstance(args, dict) else {}})
            idx = end_idx
        except json.JSONDecodeError:
            idx += 1
    return recs

# Tool schemas (OpenAI/Ollama function-calling format) — generated from the
# single ActionType registry so the agent's tools can never drift from the
# governed control endpoints (dexia.ontology.actions / dexia.api.sim_api).
TOOLS = ollama_tools()

SYSTEM_PROMPT = (
    "너는 Dexia 전장 시뮬레이터의 AI 전술 참모(AI Staff)다. "
    "주어진 전장 상황(JSON 요약)을 분석해 위협을 평가하고, 필요한 행동을 "
    "제공된 도구(tools)로 '제안'한다. 너는 직접 실행 권한이 없다 — 모든 행동은 "
    "지휘관 승인(Human-in-the-Loop)을 거친다. 따라서 행동 방침(COA)만 제안하라.\n"
    "원칙:\n"
    "1) 아군 드론이 적 AA 교전 반경(kill zone) 안에 있으면 회수(recall)를 우선 고려한다.\n"
    "2) 적 위협이 확인되고 아군이 대기(staged) 상태면 적절한 위치에 드론을 전개하거나 활성화를 제안한다.\n"
    "3) 위협이 없으면 불필요한 교전을 피한다(standby).\n"
    "평가(assessment)는 반드시 한국어로 2~3문장, 간결하게 작성하라."
)


def summarize_situation(telemetry: dict) -> str:
    """Compact, LLM-friendly battlefield summary from a telemetry record."""
    if not telemetry or "agents" not in telemetry:
        return "전장 데이터 없음 (스트리머 미가동 가능)."

    enemy = telemetry.get("enemy")
    friendly = telemetry.get("friendly")
    aa = telemetry.get("aa") or {}
    agents = telemetry.get("agents", [])
    armed = telemetry.get("armed")

    lines = [
        f"교전상태(armed): {armed}",
        f"적 진지(enemy AA) 위치: {enemy[:2] if enemy else 'N/A'} | "
        f"위협레벨: {aa.get('threat_level')} | 사거리(radar/kill): "
        f"{aa.get('radar_range')}/{aa.get('engagement_range')} | 탄약: {aa.get('ammo')}/{aa.get('max_ammo')} | "
        f"추적중: {aa.get('tracked')}",
        f"아군 진영(base) 위치: {friendly[:2] if friendly else 'N/A'}",
        f"네트워크 생존율: {round((telemetry.get('network_survivability') or 0)*100)}%",
        f"아군 드론 {len(agents)}기:",
    ]
    for a in agents:
        lines.append(
            f"  - {a['id']} ({a.get('role')}) pos=[{a['pos'][0]:.1f},{a['pos'][1]:.1f}] "
            f"alt={a.get('alt',0):.1f}m state={a.get('state')} "
            f"{'LOST('+str(a.get('loss_reason'))+')' if a.get('lost') else ''}"
        )
    return "\n".join(lines)


class TacticalAgent:
    """Ollama-backed AI Staff that proposes a COA (no auto-execution)."""

    def __init__(self, gateway: LLMGateway | None = None,
                 use_case: str = "tactical", model: str | None = None) -> None:
        # All LLM calls route through the k-LLM Gateway (multi-model + audit).
        self.gateway = gateway or get_gateway()
        self.use_case = use_case
        self.model = model  # optional override; else gateway routes by use_case

    def available(self) -> bool:
        return self.gateway.available()

    def assess(self, telemetry: dict) -> dict:
        """Return a COA: {assessment, recommendations:[{tool,kwargs}], model}."""
        if not self.gateway.available():
            return {"ok": False, "error": "LLM gateway unavailable", "recommendations": []}

        situation = summarize_situation(telemetry)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[현재 전장 상황]\n{situation}\n\n위 상황을 평가하고 필요한 행동을 제안하라."},
        ]
        g = self.gateway.chat(messages, use_case=self.use_case, tools=TOOLS, model=self.model)
        if not g.get("ok"):
            return {"ok": False, "error": f"LLM call failed: {g.get('error')}", "recommendations": []}
        resp = g["response"]
        self.model = g.get("model", self.model)

        msg = resp.get("message", {}) if isinstance(resp, dict) else getattr(resp, "message", {})
        # ollama client returns an object; normalise both dict / attr access
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)

        recommendations = []
        for tc in (tool_calls or []):
            fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", {})
            name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
            args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            recommendations.append({"tool": name, "kwargs": args or {}})

        # Fallback: many small local models emit the tool call as TEXT JSON
        # rather than a structured tool_call — recover it so the COA is reliable.
        if not recommendations:
            recommendations = _extract_tools_from_text(content)

        assessment = (content or "").strip()
        # Tool-calling models often return empty content when they call a tool.
        # Do a tools-free follow-up so the HITL card always has a Korean rationale.
        if not assessment:
            assessment = self._explain(situation, recommendations)

        return {
            "ok": True,
            "model": self.model,
            "assessment": assessment,
            "recommendations": recommendations,
        }

    def _explain(self, situation: str, recommendations: list[dict]) -> str:
        acts = ", ".join(f"{r['tool']}({r['kwargs']})" for r in recommendations) or "행동 없음(현상 유지)"
        g = self.gateway.chat(
            [
                {"role": "system", "content": "너는 Dexia 전술 참모다. 상황과 결정된 행동을 한국어 2~3문장으로 간결히 설명·정당화하라."},
                {"role": "user", "content": f"[상황]\n{situation}\n\n[결정된 행동]\n{acts}\n\n왜 이 판단이 적절한지 평가하라."},
            ],
            use_case="summary",
        )
        if not g.get("ok"):
            return ""
        r = g["response"]
        m = r.get("message", {}) if isinstance(r, dict) else getattr(r, "message", {})
        c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        return (c or "").strip()

    # ----- Phase-D hook: execute an approved recommendation via the API ----- #
    @staticmethod
    def to_api_call(rec: dict) -> dict:
        """Map a COA recommendation to its governed control-API call (after
        approval). Derived from the single ActionType registry so the tool the
        agent proposed and the endpoint the HUD calls are guaranteed in sync."""
        return _registry_to_api_call(rec)
