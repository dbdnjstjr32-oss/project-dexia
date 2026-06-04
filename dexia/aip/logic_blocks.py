"""AIP Logic Blocks — After-Action Review + Doctrine self-correction (Phase 8.5).

A lightweight, Ray-free proof-of-concept of the Palantir-AIP "Logic Block" +
"Episodic Memory" pattern: an agent reads a failed-mission log, an LLM block
diagnoses the root cause, and a deterministic block rewrites the JSON Tactical
Recipe (Doctrine) so the next run succeeds — closing a self-correction loop.

Pipeline:

    event_log ─▶ OAGEngine (doctrine + logs as context)
              ─▶ AfterActionReviewBlock(LLM)  ─▶ structured proposal {update, CoT}
              ─▶ DoctrineUpdateBlock           ─▶ recipes.json v+0.1 (+ episodic memory)

Inputs/outputs are plain dicts and JSON files — no simulator, no Ray. The LLM is
pluggable: MockLLMClient (deterministic, no API) or OllamaLLMClient (real, the
project's audited k-LLM gateway at temperature=0 with JSON output).
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RECIPES_PATH = os.path.join(_HERE, "recipes.json")
DEFAULT_MEMORY_PATH = os.path.join(_HERE, "episodic_memory.jsonl")
DEFAULT_PENDING_PATH = os.path.join(_HERE, "pending_proposals.json")  # HITL queue (Phase 8.7)

# The canonical v1.0 doctrine — also used to (re)seed recipes.json deterministically.
INITIAL_DOCTRINE: dict[str, Any] = {
    "scenario": "SEAD",
    "version": 1.0,
    "rules": {
        "min_scatter_distance_m": 10,
        "max_altitude_m": 50,
        "target_priority": "AA_RADAR",
    },
}


# --------------------------------------------------------------------------- #
# OAG — Ontology/Object-Augmented Generation context builder
# --------------------------------------------------------------------------- #
class OAGEngine:
    """Reads the current doctrine (recipes.json) + recent mission logs and folds
    them into a single typed context the LLM block reasons over."""

    def __init__(self, recipes_path: str = DEFAULT_RECIPES_PATH) -> None:
        self.recipes_path = recipes_path

    def load_doctrine(self) -> dict:
        with open(self.recipes_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def seed_doctrine(self, doctrine: Optional[dict] = None) -> dict:
        """Write the initial doctrine (used to make the loop deterministic/idempotent)."""
        doc = copy.deepcopy(doctrine or INITIAL_DOCTRINE)
        _atomic_write_json(self.recipes_path, doc)
        return doc

    def build_context(self, event_log: list[dict], doctrine: Optional[dict] = None) -> dict:
        doctrine = doctrine or self.load_doctrine()
        return {
            "doctrine": doctrine,
            "event_log": event_log,
            "summary": _summarize_log(event_log),
        }


def _summarize_log(event_log: list[dict]) -> dict:
    casualties = sum(int(e.get("casualties", 0)) for e in event_log)
    splash = [e for e in event_log if "SPLASH" in str(e.get("event", "")).upper()]
    density = next((e.get("formation_density") for e in event_log if e.get("formation_density")), None)
    return {
        "total_casualties": casualties,
        "splash_hits": len(splash),
        "formation_density": density,
        "outcome": "MISSION_FAILED" if casualties >= 3 else "MISSION_DEGRADED",
    }


# --------------------------------------------------------------------------- #
# LLM clients — pluggable transport behind a tiny .complete() interface
# --------------------------------------------------------------------------- #
class MockLLMClient:
    """Deterministic stand-in: ignores the prompt and returns a hardcoded AAR
    JSON proving the pipeline flow (no API key / network required)."""

    name = "mock"

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002
        # Parse the current spacing + splash radius out of the user prompt so the
        # mock "reasons" to a concrete new distance, but stay fully deterministic.
        cur = _first_int(re.search(r"min_scatter_distance_m[\"']?\s*[:=]\s*(\d+)", user)) or 10
        splash = _first_int(re.search(r"splash[_\s]*radius[\"']?\s*[:=]?\s*(\d+)", user, re.I)) or 40
        new_dist = max(50, splash + 10)  # clear the splash radius with margin
        return json.dumps({
            "scenario": "SEAD",
            "verdict": "MISSION_FAILED",
            "root_cause": (
                f"Formation density too tight ({cur}m). A single AA missile splash "
                f"(~{splash}m radius) engulfed the cluster, causing simultaneous casualties."
            ),
            "chain_of_thought": [
                f"The log shows AA_SPLASH_HIT with 4 casualties at formation_density {cur}m.",
                f"Inter-drone spacing ({cur}m) is well inside the missile splash radius (~{splash}m), "
                "so one detonation killed multiple drones at once.",
                f"Dispersing the swarm beyond the splash radius caps a single hit to ~1 drone, "
                f"so raise min_scatter_distance_m to {new_dist}m.",
            ],
            "proposed_update": {"min_scatter_distance_m": new_dist},
            "rationale": (
                "Spread the formation past the AA splash radius so one missile can no longer "
                "destroy the swarm; trades time-on-target for survivability."
            ),
        })


class OllamaLLMClient:
    """Real AAR via the project's audited k-LLM gateway. temperature=0 + a strict
    JSON-only instruction make the output deterministic and machine-parseable.
    Falls back to MockLLMClient transparently if the gateway is unavailable."""

    name = "ollama"

    def __init__(self, use_case: str = "doctrine") -> None:
        self.use_case = use_case
        self._fallback = MockLLMClient()
        try:
            from ..api.llm_gateway import get_gateway
            self._gw = get_gateway()
            self._ok = self._gw.available()
        except Exception:
            self._gw, self._ok = None, False

    def complete(self, system: str, user: str) -> str:
        if not self._ok:
            return self._fallback.complete(system, user)
        try:
            resp = self._gw.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                use_case=self.use_case, temperature=0.0,
            )
            if not resp.get("ok"):
                return self._fallback.complete(system, user)
            msg = resp["response"]["message"]["content"] if isinstance(resp["response"], dict) \
                else resp["response"].message.content
            return msg or self._fallback.complete(system, user)
        except Exception:
            return self._fallback.complete(system, user)


# --------------------------------------------------------------------------- #
# Logic Block 1 — After-Action Review (LLM)
# --------------------------------------------------------------------------- #
_AAR_SYSTEM = (
    "You are Dexia's After-Action Review officer. You receive a failed SEAD mission "
    "log and the current tactical doctrine, and you MUST respond with ONLY a JSON "
    "object (no prose) with this schema: "
    '{"scenario": str, "verdict": str, "root_cause": str, '
    '"chain_of_thought": [str], "proposed_update": {"<rule>": <value>}, "rationale": str}. '
    "Diagnose why the swarm was lost and propose a concrete numeric doctrine change. "
    "If drones died to AA missile splash because they flew too close, you MUST raise "
    "min_scatter_distance_m above the splash radius."
)


class AfterActionReviewBlock:
    """Takes an event log, asks the LLM_Client to diagnose the failure, and returns
    a validated structured proposal (root cause + chain-of-thought + numeric update)."""

    name = "AfterActionReviewBlock"

    def __init__(self, llm_client: Any) -> None:
        self.llm = llm_client

    def run(self, event_log: list[dict], context: dict) -> dict:
        user = (
            "CURRENT DOCTRINE:\n" + json.dumps(context["doctrine"], indent=2) +
            "\n\nMISSION OUTCOME SUMMARY:\n" + json.dumps(context["summary"], indent=2) +
            "\n\nEVENT LOG:\n" + json.dumps(event_log, indent=2) +
            "\n\nReturn ONLY the JSON proposal."
        )
        raw = self.llm.complete(_AAR_SYSTEM, user)
        proposal = _extract_json(raw)
        if proposal is None:
            raise AARParseError(f"AAR did not return parseable JSON:\n{raw[:400]}")
        # validate the essential field
        upd = proposal.get("proposed_update")
        if not isinstance(upd, dict) or not upd:
            raise AARParseError(f"AAR proposal missing 'proposed_update': {proposal}")
        proposal.setdefault("_llm", getattr(self.llm, "name", "unknown"))
        return proposal


# --------------------------------------------------------------------------- #
# Logic Block 2 — Doctrine Update (deterministic write + episodic memory)
# --------------------------------------------------------------------------- #
class DoctrineUpdateBlock:
    """Applies the AAR proposal to recipes.json: updates the named rule(s),
    increments the version (+0.1), writes the file, and appends the episode to
    the episodic-memory trail (the doctrine-evolution history)."""

    name = "DoctrineUpdateBlock"

    def __init__(self, recipes_path: str = DEFAULT_RECIPES_PATH,
                 memory_path: str = DEFAULT_MEMORY_PATH) -> None:
        self.recipes_path = recipes_path
        self.memory_path = memory_path

    def run(self, proposal: dict, event_log: Optional[list[dict]] = None) -> dict:
        with open(self.recipes_path, "r", encoding="utf-8") as f:
            doctrine = json.load(f)
        before = copy.deepcopy(doctrine)

        updates = proposal.get("proposed_update", {})
        applied: dict[str, Any] = {}
        for rule, value in updates.items():
            if rule in doctrine["rules"]:          # whitelist: only known rules
                doctrine["rules"][rule] = value
                applied[rule] = value

        doctrine["version"] = round(float(doctrine.get("version", 1.0)) + 0.1, 1)
        _atomic_write_json(self.recipes_path, doctrine)

        self._remember(before, doctrine, proposal, applied, event_log or [])
        return {
            "ok": bool(applied),
            "from_version": before["version"],
            "to_version": doctrine["version"],
            "applied": applied,
            "doctrine": doctrine,
        }

    def _remember(self, before: dict, after: dict, proposal: dict,
                  applied: dict, event_log: list[dict]) -> None:
        episode = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scenario": after.get("scenario"),
            "from_version": before["version"],
            "to_version": after["version"],
            "trigger_event": _failure_event(event_log),
            "root_cause": proposal.get("root_cause"),
            "applied": applied,
            "llm": proposal.get("_llm"),
        }
        try:
            with open(self.memory_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(episode) + "\n")
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Logic Block 2b — Doctrine Proposal (HITL gate, Phase 8.7)
# --------------------------------------------------------------------------- #
class DoctrineProposalBlock:
    """Human-in-the-loop gate: instead of overwriting recipes.json, the AI files
    its AAR proposal into ``pending_proposals.json`` for commander approval. The
    HUD surfaces it; only an [APPROVE] click (via /api/proposals) applies it to
    the live doctrine — which then cascades to the physics (Phase 8.6)."""

    name = "DoctrineProposalBlock"

    def __init__(self, recipes_path: str = DEFAULT_RECIPES_PATH,
                 pending_path: str = DEFAULT_PENDING_PATH) -> None:
        self.recipes_path = recipes_path
        self.pending_path = pending_path

    def run(self, proposal: dict, event_log: Optional[list[dict]] = None) -> dict:
        try:
            with open(self.recipes_path, "r", encoding="utf-8") as f:
                doctrine = json.load(f)
        except (OSError, json.JSONDecodeError):
            doctrine = copy.deepcopy(INITIAL_DOCTRINE)
        rules = doctrine.get("rules", {})

        # render the proposed delta as explicit from->to changes for the UI
        changes = []
        for rule, to_val in (proposal.get("proposed_update") or {}).items():
            if rule in rules:
                changes.append({"rule": rule, "from": rules[rule], "to": to_val})

        cur_version = float(doctrine.get("version", 1.0))
        pending = {
            "id": "prop_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING",
            "scenario": doctrine.get("scenario", proposal.get("scenario")),
            "from_version": round(cur_version, 1),
            "to_version": round(cur_version + 0.1, 1),
            "verdict": proposal.get("verdict"),
            "root_cause": proposal.get("root_cause"),
            "chain_of_thought": proposal.get("chain_of_thought", []),
            "rationale": proposal.get("rationale"),
            "changes": changes,
            "proposed_update": proposal.get("proposed_update", {}),
            "trigger_event": _failure_event(event_log or []),
            "llm": proposal.get("_llm"),
        }

        queue = _read_pending(self.pending_path)
        queue.append(pending)
        _atomic_write_json(self.pending_path, queue)
        return pending


def _read_pending(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class AARParseError(Exception):
    pass


def _extract_json(text: str) -> Optional[dict]:
    """Parse the first JSON object out of an LLM response (tolerates code fences)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _first_int(match) -> Optional[int]:
    return int(match.group(1)) if match else None


def _failure_event(event_log: list[dict]) -> Optional[str]:
    """The most salient event = the one with the most casualties (the failure)."""
    if not event_log:
        return None
    worst = max(event_log, key=lambda e: int(e.get("casualties", 0)))
    return worst.get("event") or next((e.get("event") for e in event_log if e.get("event")), None)


def _atomic_write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
