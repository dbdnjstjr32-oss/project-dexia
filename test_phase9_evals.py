"""Phase 9 verification — EpisodeEvalSuite + observability.

No Ray / MuJoCo: the suite is pure stdlib, so we assert the scoring math and the
threshold verdict on synthetic episodes, confirm the three immutable audit-trail
readers parse the real .jsonl logs, and check the evals_results.jsonl append +
the telemetry adapter. (The full checkpoint rollout lives in eval_phase9.py.)

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe test_phase9_evals.py
"""

from __future__ import annotations

import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dexia.evals import (
    EpisodeEvalSuite,
    EpisodeRecord,
    THRESHOLDS,
    episode_from_telemetry,
    evaluate_episode,
    observability_summary,
    read_jsonl,
)
from dexia.evals.audit import (
    ACTION_AUDIT_PATH,
    LLM_AUDIT_PATH,
    summarize_action_audit,
    summarize_llm_audit,
)


def good_episode() -> EpisodeRecord:
    return EpisodeRecord(
        scenario="unit_good", seed=1, steps=180, n_recon=2, n_kami=4,
        recon_survived=2, kami_survived=3, total_lost=1, kill_confirmed=True,
        broadcast=True, broadcast_step=120,
        net_surv_series=[1.0, 0.95, 0.9, 0.85], aa_ammo_used=6, aa_max_ammo=24,
        aa_tracked_peak=2,
    )


def bad_episode() -> EpisodeRecord:
    # no kill, heavy losses, late/absent broadcast, AA emptied on us, comms poor
    return EpisodeRecord(
        scenario="unit_bad", seed=2, steps=400, n_recon=2, n_kami=4,
        recon_survived=0, kami_survived=1, total_lost=5, kill_confirmed=False,
        broadcast=False, broadcast_step=None,
        net_surv_series=[0.5, 0.4, 0.3], aa_ammo_used=20, aa_max_ammo=24,
        aa_tracked_peak=4,
    )


def main() -> int:
    print("=" * 74)
    print("PHASE 9 — EpisodeEvalSuite + 관찰가능성")
    print("=" * 74)

    # ---- 1) metric math + direction ---------------------------------- #
    gm = {m.name: m for m in evaluate_episode(good_episode())}
    assert abs(gm["kill_efficiency"].value - (1.0 * (1 - 1 / 6))) < 1e-3
    assert gm["recon_survival"].value == 1.0
    assert abs(gm["net_survivability"].value - 0.925) < 1e-6
    assert gm["aa_engagement"].value == 0.25 and gm["aa_engagement"].direction == "min"
    assert gm["broadcast_latency"].value == 120 and gm["broadcast_latency"].passed
    assert all(m.passed for m in gm.values())
    print("\n[1] 메트릭 계산/방향 ......... OK (good 에피소드 5종 전부 통과)")

    # ---- 2) thresholds & n/a handling on a failing episode ----------- #
    bm = {m.name: m for m in evaluate_episode(bad_episode())}
    assert not bm["kill_efficiency"].passed          # no kill -> 0
    assert not bm["recon_survival"].passed           # 0/2
    assert not bm["net_survivability"].passed        # mean 0.4
    assert not bm["aa_engagement"].passed            # 20/24 > 0.5
    # broadcast never happened -> not evaluable, but counts as a fail
    assert bm["broadcast_latency"].value is None and not bm["broadcast_latency"].evaluated
    print("[2] 임계값 판정/실패 케이스 ... OK (bad 에피소드 전부 미달, broadcast=n/a)")

    # ---- 3) immutable audit-trail readers parse the real logs -------- #
    llm = summarize_llm_audit(LLM_AUDIT_PATH)
    act = summarize_action_audit(ACTION_AUDIT_PATH)
    assert isinstance(llm["calls"], int) and "models" in llm
    assert isinstance(act["submitted"], int) and "by_action" in act
    # tolerant parser: blank/garbage lines are skipped, never crash
    assert read_jsonl(os.path.join(tempfile.gettempdir(), "does_not_exist.jsonl")) == []
    obs = observability_summary()
    assert set(obs) == {"llm", "action", "ontology"}
    print(f"[3] 감사 트레일 3종 파서 ...... OK "
          f"(llm {llm['calls']}콜/ok_rate={llm['ok_rate']}, action {act['submitted']}건/"
          f"accept={act['accept_rate']}, ontology={obs['ontology']['objects']})")

    # ---- 4) suite verdict + jsonl append (temp file, no global write) - #
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "evals_results.jsonl")
        suite = EpisodeEvalSuite(results_path=path)
        # force a deterministic LLM-accuracy by passing observability explicitly
        all_ok = {"llm": {"ok_rate": 1.0, "calls": 3}, "action": act, "ontology": obs["ontology"]}
        rep_good = suite.evaluate(good_episode(), observability=all_ok)
        rep_bad = suite.evaluate(bad_episode(), observability=all_ok)
        assert rep_good["verdict"] == "PASS" and rep_good["passed"] == rep_good["evaluated"] == 6
        assert rep_bad["verdict"] == "FAIL" and "kill_efficiency" in rep_bad["failed"]
        appended = read_jsonl(path)
        assert len(appended) == 2 and appended[-1]["verdict"] == "FAIL"
        assert suite.latest()["scenario"] == "unit_bad"
        assert len(suite.history(limit=1)) == 1
    print("[4] 종합 판정 + jsonl 누적 .... OK (PASS/FAIL, 2건 append, latest/history)")

    # ---- 5) telemetry adapter (HUD live-eval path) ------------------- #
    telem = {
        "tick": 95,
        "agents": [
            {"kind": "recon", "lost": False}, {"kind": "recon", "lost": True},
            {"kind": "kami", "lost": False}, {"kind": "kami", "lost": False},
        ],
        "events": {"broadcast": True, "kill_confirmed": False, "total_lost": 1},
        "network_survivability": 0.82,
        "aa": {"ammo": 20, "max_ammo": 24, "tracked": ["agent_kami_0"]},
    }
    ep = episode_from_telemetry(telem)
    assert ep.n_recon == 2 and ep.recon_survived == 1 and ep.n_kami == 2
    assert ep.total_lost == 1 and ep.broadcast and ep.broadcast_step == 95
    assert ep.aa_ammo_used == 4 and ep.aa_max_ammo == 24 and ep.aa_tracked_peak == 1
    assert ep.net_surv_series == [0.82]
    print("[5] telemetry 어댑터(라이브) .. OK (recon 1/2, AA 4/24, broadcast@95)")

    print("\n" + "=" * 74)
    print(f"PHASE 9: 메트릭=P 임계값=P 감사트레일=P 종합판정=P 어댑터=P  "
          f"-> PASS  (thresholds={THRESHOLDS})")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
