"""MissionRunner — the multi-step tactical loop + reasoning trace (C3 + C4).

Runs a scenario to a conclusion: each cycle it advances the world (letting prior
effects resolve and the fused picture update), scores AssetMatch, decides to
collect-or-act, sends every command through the ActionBus funnel, resolves the
accepted effects, and appends a DecisionRecord to ``reasoning_trace.jsonl`` —
making *how* the AI reasoned observable, not just *what* it did.

No LLM is required; an optional gateway can narrate over the deterministic record.
See DESIGN_AIP.md sections 7.1-7.2 (the worked example this reproduces).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..fusion import EffectResolver, FusionEngine, WorldState, build_feeds
from ..ontology.action_bus import DEFAULT_AUDIT_PATH, ActionBus
from ..ontology.registry import InMemoryRegistry
from ..scenario.catalog import get_catalog
from .assetmatch import GROUND, match_assets
from .policy import decide

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_TRACE_PATH = os.path.join(_ROOT, "reasoning_trace.jsonl")
COMMIT_TICKS = 42             # how long a track stays "fires inbound" before re-allocation


@dataclass
class DecisionRecord:
    cycle: int
    tick: int
    intent: str
    perceive: dict
    fusion: list
    gaps: list
    asset_match: dict
    reasoning: str
    decisions: list
    governance: list
    events: list

    def to_dict(self) -> dict:
        return {
            "cycle": self.cycle, "tick": self.tick, "intent": self.intent,
            "perceive": self.perceive, "fusion": self.fusion, "gaps": self.gaps,
            "asset_match": self.asset_match, "reasoning": self.reasoning,
            "decisions": self.decisions, "governance": self.governance,
            "events": self.events,
        }


class MissionRunner:
    def __init__(self, scenario, catalog=None, *, max_cycles: int = 16,
                 ticks_per_cycle: int = 12, clearance: str = "commander",
                 confirm_threshold: float = 0.6, seed: int = 0,
                 trace_path: Optional[str] = DEFAULT_TRACE_PATH,
                 audit_path: str = DEFAULT_AUDIT_PATH, record_lineage: bool = False) -> None:
        self.scenario = scenario
        self.catalog = catalog or get_catalog()
        self.max_cycles = max_cycles
        self.ticks_per_cycle = ticks_per_cycle
        self.clearance = clearance
        self.confirm_threshold = confirm_threshold
        self.intent = scenario.mission.intent
        self.victory = scenario.mission.victory or {}

        self.world = WorldState.from_scenario(scenario, self.catalog)
        self.feeds = build_feeds(scenario.feeds, self.catalog)
        self.fusion = FusionEngine()
        self.resolver = EffectResolver(self.world, self.catalog)
        self.bus = ActionBus(InMemoryRegistry(), audit_path=audit_path)
        self.record_lineage = record_lineage
        self.rng = np.random.default_rng(seed)

        self.trace_path = trace_path
        self.trace: list = []
        self._committed: dict = {}           # track_id -> tick fires free up
        self._blue0 = len(self.world.blue)
        self._red_ground0 = len([e for e in self.world.red
                                 if e.category in GROUND and e.alive])
        if trace_path:                       # fresh file per run
            try:
                open(trace_path, "w", encoding="utf-8").close()
            except Exception:
                pass

    # ---- per-tick perception -------------------------------------------- #
    def _perceive(self, tick: int):
        self.world.step(1.0)
        reporting, dets = set(), []
        for f in self.feeds:
            ds = f.observe(self.world, tick, self.rng)
            if ds:
                reporting.add(f.feed_id)
            dets += ds
        self.fusion.update(dets, tick)
        return reporting, self.resolver.step(tick)

    # ---- main loop ------------------------------------------------------- #
    def run(self) -> dict:
        tick = 0
        outcome = "in_progress"
        for cycle in range(self.max_cycles):
            reporting, events = set(), []
            for _ in range(self.ticks_per_cycle):
                tick += 1
                r, ev = self._perceive(tick)
                reporting |= r
                events += [e.to_dict() for e in ev]

            tracks = self.fusion.active_tracks()
            assets = self.world.blue
            committed = {tid for tid, free in self._committed.items() if free > tick}
            matrix = match_assets(tracks, assets, self.catalog,
                                  confirm_threshold=self.confirm_threshold)
            decisions = decide(tracks, matrix, self.intent,
                               confirm_threshold=self.confirm_threshold,
                               committed=committed)

            governance = []
            for d in decisions:
                payload = {"asset_id": d.asset_id, "target": d.target,
                           "x": d.target[0], "y": d.target[1], "target_id": d.track_id}
                res = self.bus.submit(d.cmd, agent_id=d.asset_id, payload=payload,
                                      clearance=self.clearance,
                                      record_lineage=self.record_lineage)
                governance.append({"cmd": d.cmd, "asset": d.asset_id,
                                   "status": res["status"], "reason": res.get("reason")})
                if res["status"] == "accepted" and res.get("command"):
                    ev = self.resolver.submit(res["command"], tick)
                    if ev is not None:
                        events.append(ev.to_dict())
                    if d.kind in ("fires", "strike"):
                        self._committed[d.track_id] = tick + COMMIT_TICKS

            gaps = [{"track": t.track_id, "category": t.category, "conf": t.confidence,
                     "why": "conf < %.2f — 표적 불확실" % self.confirm_threshold}
                    for t in tracks if t.confidence < self.confirm_threshold]
            rec = DecisionRecord(
                cycle=cycle, tick=tick, intent=self.intent,
                perceive={"feeds": sorted(reporting), "tracks": len(tracks)},
                fusion=[t.to_dict() for t in tracks], gaps=gaps,
                asset_match={tid: [o.to_dict() for o in opts[:3]]
                             for tid, opts in matrix.items()},
                reasoning=self._narrate(decisions, gaps),
                decisions=[d.to_dict() for d in decisions], governance=governance,
                events=events,
            )
            self._emit(rec)

            outcome = self._status()
            if outcome != "in_progress":
                break
        return self._summary(outcome, tick)

    # ---- helpers --------------------------------------------------------- #
    def _narrate(self, decisions, gaps) -> str:
        if not decisions:
            return "가용 행동 없음 — 관측 지속" if not gaps else \
                "표적 불확실, 가용 ISR 없음 — 관측 지속"
        return " / ".join(f"[{d.kind}] {d.rationale}" for d in decisions)

    def _red_ground_alive(self) -> list:
        return [e for e in self.world.red if e.category in GROUND and e.alive]

    def _status(self) -> str:
        blue_lost = self._blue0 - len(self.world.blue)
        max_loss = self.victory.get("max_blue_loss")
        if max_loss is not None and blue_lost > max_loss:
            return "fail_blue_loss"
        hold = self.victory.get("hold_line")
        if hold is not None:
            if any(e.position[0] < float(hold[0]) for e in self._red_ground_alive()):
                return "fail_breach"
        if not self._red_ground_alive():
            return "success_destroyed"
        return "in_progress"

    def _emit(self, rec: DecisionRecord) -> None:
        self.trace.append(rec)
        if not self.trace_path:
            return
        try:
            with open(self.trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _summary(self, outcome: str, tick: int) -> dict:
        kinds: dict = {}
        gov_acc = gov_tot = 0
        for rec in self.trace:
            for d in rec.decisions:
                kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
            for g in rec.governance:
                gov_tot += 1
                gov_acc += 1 if g["status"] == "accepted" else 0
        return {
            "scenario": self.scenario.id, "theater": self.scenario.theater,
            "intent": self.intent, "outcome": outcome,
            "cycles": len(self.trace), "ticks": tick,
            "red_ground_total": self._red_ground0,
            "red_ground_remaining": len(self._red_ground_alive()),
            "blue_lost": self._blue0 - len(self.world.blue),
            "max_blue_loss": self.victory.get("max_blue_loss"),
            "decisions_by_kind": kinds,
            "governance_total": gov_tot, "governance_accepted": gov_acc,
            "trace_path": self.trace_path, "trace_len": len(self.trace),
        }
