"""EpisodeEvalSuite — Phase 9. The governed scorecard over a Dexia episode.

Takes an :class:`EpisodeRecord` (from a checkpoint rollout *or* a live telemetry
snapshot), scores the six AIP metrics against their thresholds, folds in the
three immutable audit trails for observability, and appends the verdict to
``evals_results.jsonl`` — itself an append-only trail of past evaluations.

    suite = EpisodeEvalSuite()
    report = suite.evaluate(episode)        # -> dict, also written to jsonl
    print(report["verdict"])                # "PASS" | "FAIL"

Pure stdlib; safe on the air-gapped evals service and in CI.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from .audit import observability_summary
from .metrics import (
    EpisodeRecord,
    evaluate_episode,
    llm_accuracy_metric,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_RESULTS_PATH = os.path.join(_ROOT, "evals_results.jsonl")


class EpisodeEvalSuite:
    def __init__(self, results_path: str = DEFAULT_RESULTS_PATH) -> None:
        self.results_path = results_path

    # ------------------------------------------------------------------ #
    def evaluate(self, ep: EpisodeRecord, *, observability: Optional[dict] = None,
                 since_ts: Optional[str] = None, write: bool = True) -> dict:
        """Score one episode and (optionally) append the report to the trail.

        ``since_ts`` scopes the audit-trail observability (llm/action) to the
        current mission/session window so a live "mission-so-far" eval reflects
        THIS run, not the all-time trail. When None (and no explicit
        ``observability`` is passed) the trails are cumulative — and the report
        labels them as such via ``observability_scope`` so nothing is mislabeled
        as episode-scoped."""
        obs = observability if observability is not None else observability_summary(since_ts=since_ts)

        metrics = evaluate_episode(ep)
        llm = obs.get("llm", {})
        metrics.append(llm_accuracy_metric(llm.get("ok_rate"), llm.get("calls", 0)))

        evaluated = [m for m in metrics if m.evaluated]
        passed = [m for m in evaluated if m.passed]
        verdict = "PASS" if evaluated and len(passed) == len(evaluated) else "FAIL"

        report = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scenario": ep.scenario,
            "seed": ep.seed,
            "steps": ep.steps,
            "verdict": verdict,
            "passed": len(passed),
            "evaluated": len(evaluated),
            "failed": [m.name for m in evaluated if not m.passed],
            "metrics": [m.to_dict() for m in metrics],
            "episode": ep.to_dict(),
            "observability": obs,
            "observability_scope": (
                f"since {since_ts}" if since_ts
                else ("provided" if observability is not None else "cumulative-all-time")
            ),
        }
        if write:
            self._append(report)
        return report

    # ------------------------------------------------------------------ #
    def _append(self, report: dict) -> None:
        try:
            with open(self.results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report) + "\n")
        except OSError:
            pass

    # ---- readers (used by the API / HUD panel) ------------------------ #
    def history(self, limit: int = 20) -> list[dict]:
        from .audit import read_jsonl
        return read_jsonl(self.results_path, limit=limit)

    def latest(self) -> Optional[dict]:
        rows = self.history(limit=1)
        return rows[-1] if rows else None


# --------------------------------------------------------------------------- #
# Adapters: build an EpisodeRecord from the world snapshots Dexia already emits.
# --------------------------------------------------------------------------- #
def episode_from_telemetry(telem: dict, scenario: str = "live") -> EpisodeRecord:
    """Construct a (single-tick) EpisodeRecord from one telemetry snapshot.

    This powers the HUD's "평가 실행" button — a *mission-so-far* eval. Series
    metrics collapse to the instantaneous value, and broadcast latency is an
    upper bound (the current tick) since a snapshot carries no history.
    """
    agents = telem.get("agents", []) or []
    recon = [a for a in agents if a.get("kind") == "recon"]
    kami = [a for a in agents if a.get("kind") == "kami"]
    events = telem.get("events", {}) or {}
    aa = telem.get("aa") or {}
    ammo, max_ammo = aa.get("ammo"), aa.get("max_ammo")
    broadcast = bool(events.get("broadcast", False))
    tick = int(telem.get("tick", 0))

    return EpisodeRecord(
        scenario=scenario,
        steps=tick,
        n_recon=len(recon),
        n_kami=len(kami),
        recon_survived=sum(1 for a in recon if not a.get("lost")),
        kami_survived=sum(1 for a in kami if not a.get("lost")),
        total_lost=int(events.get("total_lost", 0)),
        kill_confirmed=bool(events.get("kill_confirmed", False)),
        broadcast=broadcast,
        broadcast_step=tick if broadcast else None,
        net_surv_series=[float(telem.get("network_survivability", 1.0))],
        aa_ammo_used=int(max_ammo - ammo) if (isinstance(ammo, int) and isinstance(max_ammo, int)) else 0,
        aa_max_ammo=int(max_ammo) if isinstance(max_ammo, int) else 0,
        aa_tracked_peak=len(aa.get("tracked", []) or []),
    )
