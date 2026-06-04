"""Campaign evals — run the scenario library and score it (AIP build #6).

The quantitative proof that the system *does its role*: every scenario is run to
a conclusion through the agent loop and scored on whether the AI accomplished the
mission (enemy neutralised, own force preserved). Aggregates roll up by theater so
regressions in tactics show up as a falling success rate — the library is the
eval suite (DESIGN_AIP.md 8.3).

CLI:  python -m dexia.agent.campaign --count 40
      python -m dexia.agent.campaign --all
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..scenario.catalog import Catalog, get_catalog
from ..scenario.scenario import list_scenarios, load_scenario
from .loop import MissionRunner

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_RESULTS = os.path.join(_ROOT, "scenario_evals.jsonl")
PASS_SCORE = 0.7              # mission counts as "accomplished" at/above this


def score_mission(summary: dict) -> float:
    """Mission score in [0,1]: mostly how much of the enemy ground force was
    neutralised, weighted by whether the own force stayed within its loss budget,
    with a floor for a clean sweep and a penalty for letting the line break."""
    total = max(1, summary.get("red_ground_total", 1))
    neutralised = (total - summary.get("red_ground_remaining", total)) / total
    max_loss = summary.get("max_blue_loss")
    blue_ok = (max_loss is None) or (summary.get("blue_lost", 0) <= max_loss)
    score = 0.7 * neutralised + 0.3 * (1.0 if blue_ok else 0.0)
    outcome = summary.get("outcome")
    if outcome == "success_destroyed":
        score = max(score, 0.95)
    elif outcome == "fail_breach":
        score *= 0.5
    elif outcome == "fail_blue_loss":
        score *= 0.6
    return round(min(1.0, max(0.0, score)), 3)


@dataclass
class MissionResult:
    scenario: str
    theater: str
    intent: str
    outcome: str
    score: float
    neutralised: int
    red_total: int
    blue_lost: int
    cycles: int
    decisions_by_kind: dict = field(default_factory=dict)
    governance_accept_rate: float = 0.0

    @property
    def passed(self) -> bool:
        return self.score >= PASS_SCORE


def evaluate_scenario(path: str, catalog: Optional[Catalog] = None, *,
                      max_cycles: int = 16, **runner_kw) -> MissionResult:
    catalog = catalog or get_catalog()
    sc = load_scenario(path, validate=False, catalog=catalog)
    tmp = tempfile.gettempdir()
    runner = MissionRunner(
        sc, catalog, max_cycles=max_cycles, record_lineage=False,
        trace_path=runner_kw.pop("trace_path", os.path.join(tmp, "dexia_campaign_trace.jsonl")),
        audit_path=runner_kw.pop("audit_path", os.path.join(tmp, "dexia_campaign_audit.jsonl")),
        **runner_kw)
    s = runner.run()
    gov_rate = (s["governance_accepted"] / s["governance_total"]
                if s["governance_total"] else 0.0)
    return MissionResult(
        scenario=s["scenario"], theater=s["theater"], intent=s["intent"],
        outcome=s["outcome"], score=score_mission(s),
        neutralised=s["red_ground_total"] - s["red_ground_remaining"],
        red_total=s["red_ground_total"], blue_lost=s["blue_lost"],
        cycles=s["cycles"], decisions_by_kind=s["decisions_by_kind"],
        governance_accept_rate=round(gov_rate, 3),
    )


@dataclass
class CampaignReport:
    results: list = field(default_factory=list)

    def aggregates(self) -> dict:
        n = len(self.results) or 1
        return {
            "scenarios": len(self.results),
            "pass_rate": round(sum(r.passed for r in self.results) / n, 3),
            "mean_score": round(sum(r.score for r in self.results) / n, 3),
            "mean_neutralised_frac": round(
                sum(r.neutralised / max(1, r.red_total) for r in self.results) / n, 3),
            "mean_blue_lost": round(sum(r.blue_lost for r in self.results) / n, 3),
            "outcomes": _counter(r.outcome for r in self.results),
        }

    def by_theater(self) -> dict:
        out: dict = {}
        for r in self.results:
            out.setdefault(r.theater, []).append(r)
        return {t: {"scenarios": len(rs),
                    "pass_rate": round(sum(x.passed for x in rs) / len(rs), 3),
                    "mean_score": round(sum(x.score for x in rs) / len(rs), 3)}
                for t, rs in sorted(out.items())}


def _counter(it) -> dict:
    c: dict = {}
    for x in it:
        c[x] = c.get(x, 0) + 1
    return c


def evaluate_library(paths: Optional[list] = None, catalog: Optional[Catalog] = None,
                     *, results_path: Optional[str] = DEFAULT_RESULTS,
                     max_cycles: int = 16, limit: Optional[int] = None) -> CampaignReport:
    from ..scenario.generator import GENERATED_DIR
    catalog = catalog or get_catalog()
    paths = paths if paths is not None else list_scenarios(GENERATED_DIR)
    if limit:
        paths = paths[:limit]
    if results_path:
        try:
            open(results_path, "w", encoding="utf-8").close()
        except Exception:
            results_path = None
    report = CampaignReport()
    for p in paths:
        r = evaluate_scenario(p, catalog, max_cycles=max_cycles)
        report.results.append(r)
        if results_path:
            with open(results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return report


def main(argv=None) -> int:
    import sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Run + score the Dexia scenario library.")
    ap.add_argument("--count", type=int, default=20, help="evaluate the first N scenarios")
    ap.add_argument("--all", action="store_true", help="evaluate the whole library")
    ap.add_argument("--max-cycles", type=int, default=16)
    args = ap.parse_args(argv)

    report = evaluate_library(limit=None if args.all else args.count,
                              max_cycles=args.max_cycles)
    agg = report.aggregates()
    print("=" * 70)
    print(f"CAMPAIGN EVALS — {agg['scenarios']} scenarios")
    print("=" * 70)
    print(f"  pass rate (score>={PASS_SCORE}) : {agg['pass_rate']:.0%}")
    print(f"  mean mission score        : {agg['mean_score']}")
    print(f"  mean enemy neutralised    : {agg['mean_neutralised_frac']:.0%}")
    print(f"  mean blue lost            : {agg['mean_blue_lost']}")
    print(f"  outcomes                  : {agg['outcomes']}")
    print("  by theater:")
    for t, m in report.by_theater().items():
        print(f"    {t:<16} pass {m['pass_rate']:.0%}  score {m['mean_score']}  (n={m['scenarios']})")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
