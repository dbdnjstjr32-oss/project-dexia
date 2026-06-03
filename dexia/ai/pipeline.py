"""TacticalPipeline — the AIP Logic block chain (Phase 8).

Runs the ontology snapshot through CommsRisk -> TacticalAssess(LLM) ->
RouteOptim -> KillChainDecision(LLM) -> MissionUpdate(ActionBus), producing a
governed Course of Action. Replaces the frontend mockRag rule-base entirely.
"""

from __future__ import annotations

from .blocks import (
    CommsRiskBlock,
    KillChainDecisionBlock,
    MissionUpdateBlock,
    RouteOptimBlock,
    TacticalAssessBlock,
)
from .oag import build_oag_context


class TacticalPipeline:
    def __init__(self, gateway=None) -> None:
        self.blocks = [
            CommsRiskBlock(),
            TacticalAssessBlock(gateway),
            RouteOptimBlock(),
            KillChainDecisionBlock(gateway),
            MissionUpdateBlock(),
        ]

    def run(self, snapshot: dict, friendly_base=None) -> dict:
        ctx = {
            "snapshot": snapshot,
            "oag_context": build_oag_context(snapshot),
            "friendly_base": friendly_base or [0.0, 0.0],
            "recommendations": [],
            "assessment": "",
        }
        trace = []
        for block in self.blocks:
            ctx = block.run(ctx)
            trace.append(block.name)
        return {
            "ok": True,
            "model": ctx.get("model"),
            "assessment": ctx.get("assessment", ""),
            "recommendations": ctx.get("recommendations", []),
            "comms": ctx.get("comms"),
            "killchain_decision": ctx.get("killchain_decision"),
            "blocks": trace,
            "oag_context": ctx["oag_context"],
        }
