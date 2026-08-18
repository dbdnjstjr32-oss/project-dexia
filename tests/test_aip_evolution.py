"""Phase 8.5 verification — AIP doctrine self-correction loop (AAR → update).

End-to-end proof that the AI performs an After-Action Review on a failed-mission
log and autonomously rewrites its JSON Tactical Recipe to survive the next run:

    v1.0 (scatter=10m)  ──fail──▶  AAR diagnoses splash death  ──▶  v1.1 (scatter=50m)

Dual-mode: runs under ``pytest tests/test_aip_evolution.py`` *and* directly
(``python tests/test_aip_evolution.py``) — the latter prints the chain-of-thought
and the final JSON payload. Deterministic (MockLLMClient); no Ray, no network.
"""

from __future__ import annotations

import json
import os
import sys

# allow running directly (python tests/test_aip_evolution.py) from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.aip import (
    INITIAL_DOCTRINE,
    DEFAULT_RECIPES_PATH,
    OAGEngine,
    MockLLMClient,
    AfterActionReviewBlock,
    DoctrineUpdateBlock,
)

# A hardcoded "Mission Failed" log — Operation Blindspot (SEAD): the swarm flew
# 10 m apart and a single AA missile splash wiped out four drones at once.
FAILED_MISSION_LOG = [
    {"tick": 12, "event": "INGRESS", "formation_density": "10m"},
    {"tick": 100, "event": "AA_SPLASH_HIT", "casualties": 4,
     "formation_density": "10m", "splash_radius_m": 40},
    {"tick": 101, "event": "MISSION_ABORT", "survivors": 0},
]


def _reset_doctrine() -> OAGEngine:
    """Seed recipes.json back to the canonical v1.0 so the loop is idempotent."""
    oag = OAGEngine(DEFAULT_RECIPES_PATH)
    oag.seed_doctrine(INITIAL_DOCTRINE)
    return oag


# --------------------------------------------------------------------------- #
# pytest test functions
# --------------------------------------------------------------------------- #
def test_initial_doctrine_is_v1():
    oag = _reset_doctrine()
    doc = oag.load_doctrine()
    assert doc["version"] == 1.0
    assert doc["rules"]["min_scatter_distance_m"] == 10
    assert doc["scenario"] == "SEAD"


def test_aar_block_proposes_scatter_increase():
    oag = _reset_doctrine()
    ctx = oag.build_context(FAILED_MISSION_LOG)
    assert ctx["summary"]["outcome"] == "MISSION_FAILED"
    proposal = AfterActionReviewBlock(MockLLMClient()).run(FAILED_MISSION_LOG, ctx)
    assert "proposed_update" in proposal and proposal["chain_of_thought"]
    new = proposal["proposed_update"]["min_scatter_distance_m"]
    assert new >= 50, f"AAR must disperse beyond the 40m splash radius, got {new}"


def test_doctrine_update_persists_to_disk():
    """The core requirement: recipes.json is physically modified on disk."""
    oag = _reset_doctrine()
    ctx = oag.build_context(FAILED_MISSION_LOG)
    proposal = AfterActionReviewBlock(MockLLMClient()).run(FAILED_MISSION_LOG, ctx)
    result = DoctrineUpdateBlock(DEFAULT_RECIPES_PATH).run(proposal, FAILED_MISSION_LOG)

    assert result["ok"] and result["to_version"] == 1.1
    # re-read the file from disk (not the in-memory object) to prove persistence
    with open(DEFAULT_RECIPES_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["version"] == 1.1
    assert on_disk["rules"]["min_scatter_distance_m"] == 50
    # untouched rules are preserved
    assert on_disk["rules"]["target_priority"] == "AA_RADAR"
    assert on_disk["rules"]["max_altitude_m"] == 50


# --------------------------------------------------------------------------- #
# direct-run demo (chain-of-thought + payloads)
# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA Phase 8.5 — AIP Doctrine Self-Correction (Operation Blindspot / SEAD)")
    print(bar)

    oag = _reset_doctrine()

    # Step 1 — initial doctrine
    initial = oag.load_doctrine()
    print("\n[1] INITIAL DOCTRINE (recipes.json)")
    print(f"    version={initial['version']}  "
          f"min_scatter_distance_m={initial['rules']['min_scatter_distance_m']}  "
          f"target_priority={initial['rules']['target_priority']}")

    # Step 2 — inject the failed-mission log into the AAR block
    print("\n[2] INJECT FAILED-MISSION LOG → AfterActionReviewBlock (LLM=mock, temp=0)")
    for e in FAILED_MISSION_LOG:
        print(f"      {e}")
    ctx = oag.build_context(FAILED_MISSION_LOG)
    proposal = AfterActionReviewBlock(MockLLMClient()).run(FAILED_MISSION_LOG, ctx)

    print(f"\n    ── AI After-Action Review (Chain of Thought) [{proposal.get('_llm')}] ──")
    print(f"    verdict   : {proposal['verdict']}")
    print(f"    root_cause: {proposal['root_cause']}")
    for i, step in enumerate(proposal["chain_of_thought"], 1):
        print(f"      {i}. {step}")
    print(f"    proposed_update: {json.dumps(proposal['proposed_update'])}")

    # Step 3 — apply the learned solution
    print("\n[3] DoctrineUpdateBlock → rewrite recipes.json + episodic memory")
    result = DoctrineUpdateBlock(DEFAULT_RECIPES_PATH).run(proposal, FAILED_MISSION_LOG)
    print(f"    applied {result['applied']}  "
          f"version {result['from_version']} → {result['to_version']}")

    # Step 4 — prove the file changed on disk
    with open(DEFAULT_RECIPES_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    print("\n[4] UPDATED DOCTRINE (re-read from disk)")
    print(json.dumps(on_disk, indent=2))

    ok = (on_disk["version"] == 1.1
          and on_disk["rules"]["min_scatter_distance_m"] == 50)
    print("\n" + bar)
    print(f"SELF-CORRECTION {'VERIFIED ✅  (v1.0 scatter=10 → v1.1 scatter=50)' if ok else 'FAILED ❌'}")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
