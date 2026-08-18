"""dexia.agent — the tactical agent loop (AIP climax: C3 + C4).

Ties fusion + ontology governance + equipment into a multi-step loop that does
NOT solve in one click: it perceives, fuses, scores feasible asset/track pairings
(deterministic AssetMatch), decides whether to *collect* (task ISR on a vague
track) or *act* (operate the right asset), routes every command through the
ActionBus funnel, resolves effects, and re-perceives — emitting a DecisionRecord
each cycle so the reasoning is observable (reasoning_trace.jsonl).

The decision logic is deterministic (testable without an LLM); the LLM layer,
when present, narrates and re-prioritises on top. See DESIGN_AIP.md section 7.
"""

from __future__ import annotations

from .assetmatch import Option, match_assets
from .loop import DecisionRecord, MissionRunner
from .policy import Decision, decide

__all__ = [
    "match_assets",
    "Option",
    "decide",
    "Decision",
    "MissionRunner",
    "DecisionRecord",
]
