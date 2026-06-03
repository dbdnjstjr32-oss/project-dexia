"""Immutable audit-trail readers — Phase 9 observability.

Dexia already appends three governed trails as it runs:
  * ``action_audit.jsonl``   — every ActionBus submit (accepted/rejected + reason)
  * ``llm_audit.jsonl``      — every k-LLM Gateway call (model, latency, tokens)
  * ``ontology_state.json``  — the latest OSS snapshot (object/link counts)

These are append-only / overwrite-latest by design (a tamper-evident trail). This
module only *reads* them — it never mutates — and reduces each to a summary the
EpisodeEvalSuite and the HUD Evals panel render.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ACTION_AUDIT_PATH = os.path.join(_ROOT, "action_audit.jsonl")
LLM_AUDIT_PATH = os.path.join(_ROOT, "llm_audit.jsonl")
ONTOLOGY_STATE_PATH = os.path.join(_ROOT, "ontology_state.json")


def read_jsonl(path: str, limit: Optional[int] = None) -> list[dict]:
    """Parse a .jsonl trail tolerantly (skip half-written / blank lines).

    ``limit`` returns only the most recent N records (tail)."""
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:] if limit else rows


# --------------------------------------------------------------------------- #
def summarize_llm_audit(path: str = LLM_AUDIT_PATH) -> dict:
    rows = read_jsonl(path)
    calls = len(rows)
    ok = sum(1 for r in rows if r.get("status") == "ok")
    errors = calls - ok
    latencies = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
    models = Counter(r.get("model") for r in rows if r.get("model"))
    use_cases = Counter(r.get("use_case") for r in rows if r.get("use_case"))
    prompt_tok = sum(r.get("prompt_tokens") or 0 for r in rows)
    completion_tok = sum(r.get("completion_tokens") or 0 for r in rows)
    return {
        "calls": calls,
        "ok": ok,
        "errors": errors,
        "ok_rate": round(ok / calls, 4) if calls else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "models": dict(models),
        "use_cases": dict(use_cases),
        "prompt_tokens": prompt_tok,
        "completion_tokens": completion_tok,
    }


def summarize_action_audit(path: str = ACTION_AUDIT_PATH) -> dict:
    rows = read_jsonl(path)
    submitted = len(rows)
    accepted = sum(1 for r in rows if r.get("status") == "accepted")
    rejected = submitted - accepted
    by_action = Counter(r.get("action") for r in rows if r.get("action"))
    modes = Counter(r.get("mode") for r in rows if r.get("mode"))
    reasons = Counter(r.get("reason") for r in rows if r.get("status") == "rejected" and r.get("reason"))
    return {
        "submitted": submitted,
        "accepted": accepted,
        "rejected": rejected,
        "accept_rate": round(accepted / submitted, 4) if submitted else None,
        "by_action": dict(by_action),
        "modes": dict(modes),
        "reject_reasons": dict(reasons),
    }


def summarize_ontology_state(path: str = ONTOLOGY_STATE_PATH) -> dict:
    """Counts from the latest OSS snapshot. Tolerates the (optional) .jsonl trail
    variant — if a sibling ``.jsonl`` exists we report the snapshot count too."""
    snap: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            snap = {}
    objects = snap.get("objects", {}) if isinstance(snap, dict) else {}
    counts = {t: len(v) for t, v in objects.items()}
    jsonl = os.path.splitext(path)[0] + ".jsonl"
    snapshots = len(read_jsonl(jsonl)) if os.path.exists(jsonl) else (1 if snap else 0)
    return {
        "objects": counts,
        "links": len(snap.get("links", [])) if isinstance(snap, dict) else 0,
        "snapshots": snapshots,
    }


def observability_summary(
    action_path: str = ACTION_AUDIT_PATH,
    llm_path: str = LLM_AUDIT_PATH,
    ontology_path: str = ONTOLOGY_STATE_PATH,
) -> dict:
    """The full three-trail observability block embedded in every eval report."""
    return {
        "llm": summarize_llm_audit(llm_path),
        "action": summarize_action_audit(action_path),
        "ontology": summarize_ontology_state(ontology_path),
    }
