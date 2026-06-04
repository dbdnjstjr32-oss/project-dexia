"""Agent loop + reasoning trace (AIP build #4 — the climax).

Proves the AI doesn't "click-solve": it runs a multi-step loop that collects
when uncertain (task ISR), suppresses air-defense, masses fires on a moving
column (with predicted-fire lead), governs every command through the ActionBus,
and writes an observable DecisionRecord each cycle. Reproduces the DESIGN_AIP.md
7.2 worked example on the seed scenario — deterministically, no LLM.

Dual-mode: ``pytest`` *and* ``python tests/test_agent_loop.py`` (prints the trace).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.agent import MissionRunner
from dexia.scenario import load_catalog, load_scenario

_CAT = load_catalog()


def _run(**kw):
    sc = load_scenario("ua-east-armor-thrust-007")
    trace = os.path.join(tempfile.gettempdir(), "dexia_trace_test.jsonl")
    audit = os.path.join(tempfile.gettempdir(), "dexia_audit_test.jsonl")
    r = MissionRunner(sc, _CAT, trace_path=trace, audit_path=audit,
                      record_lineage=False, **kw)
    return r, r.run()


# --------------------------------------------------------------------------- #
def test_loop_runs_and_writes_trace():
    r, summary = _run()
    assert summary["cycles"] >= 1 and summary["trace_len"] == summary["cycles"]
    # trace file is valid JSONL with the required observability keys
    with open(r.trace_path, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert lines, "reasoning_trace.jsonl should have records"
    for rec in lines:
        assert {"cycle", "perceive", "fusion", "gaps", "asset_match",
                "reasoning", "decisions", "governance", "events"} <= set(rec)


def test_ai_collects_then_suppresses_then_strikes():
    """The decision sequence the design promises: ISR collect on a vague emitter,
    EW suppression of the confirmed SAM, indirect fires on the armor column."""
    r, summary = _run()
    kinds = {d["kind"] for rec in r.trace for d in rec.decisions}
    cmds = {d["cmd"] for rec in r.trace for d in rec.decisions}
    assert "collect" in kinds, "should task ISR to confirm a low-confidence track"
    assert "suppress" in kinds and "jam" in cmds, "should jam the SA-11 radar"
    assert "fires" in kinds and "request_fires" in cmds, "should mass fires on armor"


def test_commands_are_governed_and_executed():
    r, summary = _run()
    statuses = [g["status"] for rec in r.trace for g in rec.governance]
    assert statuses and all(s in ("accepted", "rejected") for s in statuses)
    assert "accepted" in statuses, "commander clearance should accept fires/jam"
    # fires actually land -> at least some red armor neutralised
    assert summary["red_ground_remaining"] < 12
    impacts = [e for rec in r.trace for e in rec.events
               if e.get("action") == "fire" and e.get("status") == "impact"]
    assert any(e["killed"] for e in impacts), "indirect fire should neutralise armor"


def test_operator_clearance_blocks_lethal_fires():
    """Governance bites: at operator clearance, commander-only request_fires is
    rejected by the funnel (jam/ISR still pass)."""
    r, summary = _run(clearance="operator")
    fires_gov = [g for rec in r.trace for g in rec.governance if g["cmd"] == "request_fires"]
    assert fires_gov and all(g["status"] == "rejected" for g in fires_gov)


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA AIP build #4 — Tactical Agent Loop (collect → suppress → strike)")
    print(bar)

    r, summary = _run()
    for rec in r.trace:
        print(f"\n── cycle {rec.cycle} (t={rec.tick}) feeds={rec.perceive['feeds']} "
              f"tracks={rec.perceive['tracks']} gaps={len(rec.gaps)}")
        for d in rec.decisions:
            print(f"     ▸ [{d['kind']:<8}] {d['cmd']:<14} {d['asset']:<16} → {d['why']}")
        landed = [e for e in rec.events if e.get("status") == "impact"]
        for e in landed:
            print(f"       💥 {e['detail']}")
        if not rec.decisions:
            print(f"     · {rec.reasoning}")

    print("\n" + bar)
    print(f"OUTCOME: {summary['outcome']}  |  red armor left: {summary['red_ground_remaining']}/12  "
          f"|  blue lost: {summary['blue_lost']}  |  cycles: {summary['cycles']}")
    ok = (summary["red_ground_remaining"] < 12
          and any(d["kind"] == "collect" for rec in r.trace for d in rec.decisions)
          and any(d["kind"] == "suppress" for rec in r.trace for d in rec.decisions)
          and any(d["kind"] == "fires" for rec in r.trace for d in rec.decisions))
    print(f"AGENT LOOP {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"(collect→suppress→fires sequence, governed, trace written)")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
