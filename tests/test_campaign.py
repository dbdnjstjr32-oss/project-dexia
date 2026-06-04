"""Campaign evals harness (AIP build #6 — quantitative proof of role).

Proves the library doubles as an eval suite: scenarios run to a conclusion and
are scored, aggregates roll up, and results persist. This is the capstone that
answers "does the system do its job?" with a number, per theater.

Dual-mode: ``pytest`` *and* ``python tests/test_campaign.py``.
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

from dexia.agent.campaign import (
    PASS_SCORE,
    evaluate_library,
    evaluate_scenario,
    score_mission,
)
from dexia.scenario import load_catalog
from dexia.scenario.generator import GENERATED_DIR
from dexia.scenario.scenario import list_scenarios

_CAT = load_catalog()


# --------------------------------------------------------------------------- #
def test_score_mission_bounds_and_logic():
    # clean sweep floors high
    assert score_mission({"red_ground_total": 10, "red_ground_remaining": 0,
                          "blue_lost": 0, "max_blue_loss": 2,
                          "outcome": "success_destroyed"}) >= 0.95
    # did nothing -> low
    assert score_mission({"red_ground_total": 10, "red_ground_remaining": 10,
                          "blue_lost": 0, "max_blue_loss": 2,
                          "outcome": "in_progress"}) < 0.4
    # breach penalised
    breached = score_mission({"red_ground_total": 10, "red_ground_remaining": 3,
                             "blue_lost": 0, "max_blue_loss": 2, "outcome": "fail_breach"})
    clean = score_mission({"red_ground_total": 10, "red_ground_remaining": 3,
                          "blue_lost": 0, "max_blue_loss": 2, "outcome": "in_progress"})
    assert breached < clean


def test_evaluate_single_scenario():
    path = list_scenarios(GENERATED_DIR)[0]
    r = evaluate_scenario(path, _CAT, max_cycles=12)
    assert 0.0 <= r.score <= 1.0
    assert r.red_total >= 1 and 0 <= r.neutralised <= r.red_total
    assert r.outcome in ("in_progress", "success_destroyed", "fail_breach", "fail_blue_loss")
    assert 0.0 <= r.governance_accept_rate <= 1.0


def test_campaign_aggregates_and_persists():
    results_path = os.path.join(tempfile.gettempdir(), "dexia_campaign_test.jsonl")
    report = evaluate_library(limit=16, results_path=results_path, max_cycles=12)
    agg = report.aggregates()
    assert agg["scenarios"] == 16
    assert 0.0 <= agg["pass_rate"] <= 1.0 and 0.0 <= agg["mean_score"] <= 1.0
    # the agent is competent on the matched library — sanity floor, not a tight bar
    assert agg["mean_score"] >= 0.5, f"mean score unexpectedly low: {agg}"
    # results persisted as JSONL
    with open(results_path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    assert len(rows) == 16 and all("score" in r for r in rows)
    # per-theater breakdown present
    bt = report.by_theater()
    assert bt and all("pass_rate" in v for v in bt.values())


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 70
    print(bar)
    print("DEXIA AIP build #6 — Campaign Evals (quantitative proof of role)")
    print(bar)
    report = evaluate_library(limit=40, results_path=None, max_cycles=14)
    agg = report.aggregates()
    print(f"\n  {agg['scenarios']} scenarios | pass {agg['pass_rate']:.0%} | "
          f"mean score {agg['mean_score']} | neutralised {agg['mean_neutralised_frac']:.0%} | "
          f"blue lost {agg['mean_blue_lost']}")
    print(f"  outcomes: {agg['outcomes']}")
    for t, m in report.by_theater().items():
        print(f"    {t:<16} pass {m['pass_rate']:.0%}  score {m['mean_score']}  (n={m['scenarios']})")
    ok = agg["mean_score"] >= 0.5 and agg["pass_rate"] >= 0.5
    print("\n" + bar)
    print(f"CAMPAIGN EVALS {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"(library scored end-to-end, {agg['pass_rate']:.0%} pass @ score>={PASS_SCORE})")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
