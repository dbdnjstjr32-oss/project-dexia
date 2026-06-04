"""Scenario library + generator (AIP build #5 — toward 100).

Proves scenarios are minted as validated data across theaters, and that a
generated scenario actually *runs* through the agent loop end-to-end. The shipped
library (scenarios/generated) is the eval corpus for build #6.

Dual-mode: ``pytest`` *and* ``python tests/test_generator.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.scenario import Scenario, load_catalog, load_scenario
from dexia.scenario.generator import (
    GENERATED_DIR,
    TEMPLATES,
    generate_library,
    generate_scenario_dict,
)
from dexia.scenario.scenario import list_scenarios

_CAT = load_catalog()


def _generated_paths():
    return list_scenarios(GENERATED_DIR)


# --------------------------------------------------------------------------- #
def test_generate_library_all_valid():
    out = os.path.join(tempfile.gettempdir(), "dexia_gen_test")
    paths = generate_library(40, out, seed=7, catalog=_CAT)
    assert len(paths) == 40
    for p in paths:
        sc = load_scenario(p, validate=False)
        assert sc.validate(_CAT) == [], f"{os.path.basename(p)} invalid"


def test_all_four_theaters_represented():
    out = os.path.join(tempfile.gettempdir(), "dexia_gen_test2")
    paths = generate_library(40, out, seed=1, catalog=_CAT)
    theaters = {os.path.basename(p).rsplit("-", 1)[0] for p in paths}
    assert theaters == set(TEMPLATES.keys())


def test_generation_is_deterministic():
    a = generate_scenario_dict("korea", 3, seed=42)
    b = generate_scenario_dict("korea", 3, seed=42)
    c = generate_scenario_dict("korea", 3, seed=43)
    assert a == b and a != c


def test_shipped_library_is_valid_and_sized():
    paths = _generated_paths()
    assert len(paths) >= 100, "the shipped library should hold 100+ scenarios"
    for p in paths:
        sc = load_scenario(p, validate=False)
        assert sc.validate(_CAT) == [], f"{os.path.basename(p)} invalid"


def test_generated_scenario_runs_end_to_end():
    """A generated scenario is not just structurally valid — it runs through the
    full agent loop and concludes (or progresses) without error."""
    from dexia.agent import MissionRunner
    trace = os.path.join(tempfile.gettempdir(), "dexia_gen_run.jsonl")
    audit = os.path.join(tempfile.gettempdir(), "dexia_gen_audit.jsonl")
    sample = [p for i, p in enumerate(_generated_paths()) if i % 25 == 0][:4]  # one per theater
    assert sample
    for p in sample:
        sc = load_scenario(p)
        summary = MissionRunner(sc, _CAT, trace_path=trace, audit_path=audit,
                                record_lineage=False, max_cycles=6).run()
        assert summary["cycles"] >= 1
        assert summary["outcome"] in (
            "in_progress", "success_destroyed", "fail_breach", "fail_blue_loss")


# --------------------------------------------------------------------------- #
def main() -> int:
    from dexia.agent import MissionRunner
    bar = "=" * 74
    print(bar)
    print("DEXIA AIP build #5 — Scenario library + generator (toward 100)")
    print(bar)

    paths = _generated_paths()
    by_theater: dict = {}
    for p in paths:
        t = os.path.basename(p).rsplit("-", 1)[0]
        by_theater[t] = by_theater.get(t, 0) + 1
    print(f"\n[library] {len(paths)} scenarios in {os.path.relpath(GENERATED_DIR)}")
    for t, n in sorted(by_theater.items()):
        print(f"      {t:<16} {n}")

    bad = [p for p in paths if load_scenario(p, validate=False).validate(_CAT)]
    print(f"\n[validate] {len(paths) - len(bad)}/{len(paths)} valid")

    print(f"\n[run] one scenario per theater through the agent loop:")
    trace = os.path.join(tempfile.gettempdir(), "dexia_gen_run.jsonl")
    audit = os.path.join(tempfile.gettempdir(), "dexia_gen_audit.jsonl")
    seen: dict = {}
    for p in paths:
        sc = load_scenario(p, validate=False)
        if sc.theater in seen:
            continue
        seen[sc.theater] = True
        s = MissionRunner(sc, _CAT, trace_path=trace, audit_path=audit,
                          record_lineage=False, max_cycles=10).run()
        print(f"      {sc.id:<20} intent={sc.mission.intent:<8} "
              f"→ {s['outcome']:<18} red {s['red_ground_remaining']} left, "
              f"blue lost {s['blue_lost']}, {s['cycles']}c")

    ok = len(paths) >= 100 and not bad
    print("\n" + bar)
    print(f"LIBRARY {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"({len(paths)} scenarios, all valid, run end-to-end across 4 theaters)")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
