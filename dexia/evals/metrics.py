"""Episode metrics + thresholds — Phase 9 Evals.

A small declarative metric system: each metric has a *direction* (higher- or
lower-is-better) and a *threshold*, so the suite can render an automatic
pass/fail verdict (the AIP plan's "임계값 기준 자동 판정 리포트").

Pure stdlib — no Ray / MuJoCo — so the eval layer runs on the air-gapped
``dexia-evals`` service (Phase 10) and in CI without a cluster.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# EpisodeRecord — the canonical, interpreter-agnostic input to the suite.
# Produced by a live rollout (eval_phase9.py) OR from a telemetry snapshot
# (live HUD eval). Either way the metric math below is identical.
# --------------------------------------------------------------------------- #
@dataclass
class EpisodeRecord:
    scenario: str = "interactive"
    seed: Optional[int] = None
    steps: int = 0
    n_recon: int = 0
    n_kami: int = 0
    recon_survived: int = 0
    kami_survived: int = 0
    total_lost: int = 0
    kill_confirmed: bool = False
    broadcast: bool = False
    broadcast_step: Optional[int] = None
    kill_step: Optional[int] = None
    net_surv_series: list = field(default_factory=list)
    aa_ammo_used: int = 0
    aa_max_ammo: int = 0
    aa_tracked_peak: int = 0

    @property
    def total_drones(self) -> int:
        return self.n_recon + self.n_kami

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# MetricResult — one scored line of the report.
# --------------------------------------------------------------------------- #
@dataclass
class MetricResult:
    name: str
    label: str
    value: Optional[float]      # None => not evaluable (n/a), excluded from verdict
    threshold: float
    direction: str              # "max" (>=) | "min" (<=)
    unit: str = ""
    note: str = ""

    @property
    def evaluated(self) -> bool:
        return self.value is not None

    @property
    def passed(self) -> bool:
        if self.value is None:
            return False
        if self.direction == "max":
            return self.value >= self.threshold
        return self.value <= self.threshold

    def to_dict(self) -> dict:
        d = asdict(self)
        d["passed"] = self.passed
        d["evaluated"] = self.evaluated
        return d


# Thresholds — tunable here (Phase-10 dexia.config.yaml can override).
THRESHOLDS = {
    "kill_efficiency": 0.50,     # mission success weighted by surviving force
    "recon_survival": 0.50,      # fraction of recon drones that live
    "net_survivability": 0.70,   # mean comms network survivability
    "aa_engagement": 0.50,       # fraction of AA ammo it expended on us (lower better)
    "broadcast_latency": 250,    # ticks to first target broadcast (lower better)
    "llm_accuracy": 0.90,        # fraction of LLM calls that returned ok
}


def _safe_div(a: float, b: float) -> Optional[float]:
    return (a / b) if b else None


def evaluate_episode(ep: EpisodeRecord) -> list[MetricResult]:
    """The five *episode-level* metrics. ``llm_accuracy`` (the sixth, from the
    audit trail) is appended by the suite, which owns the audit readers."""
    total = ep.total_drones

    # 1) Kill Efficiency — did we kill, and how much force did it cost?
    if total:
        eff = (1.0 if ep.kill_confirmed else 0.0) * (1.0 - ep.total_lost / total)
    else:
        eff = None
    kill_eff = MetricResult(
        "kill_efficiency", "Kill Efficiency", _round(eff),
        THRESHOLDS["kill_efficiency"], "max", "",
        note=("kill confirmed" if ep.kill_confirmed else "no kill") +
             f", lost {ep.total_lost}/{total}",
    )

    # 2) Recon Survival
    recon_surv = MetricResult(
        "recon_survival", "Recon Survival",
        _round(_safe_div(ep.recon_survived, ep.n_recon)),
        THRESHOLDS["recon_survival"], "max", "",
        note=f"{ep.recon_survived}/{ep.n_recon} recon alive",
    )

    # 3) Network Survivability (mean over the episode)
    ns = (sum(ep.net_surv_series) / len(ep.net_surv_series)) if ep.net_surv_series else None
    net_surv = MetricResult(
        "net_survivability", "Net Survivability", _round(ns),
        THRESHOLDS["net_survivability"], "max", "",
        note=f"mean over {len(ep.net_surv_series)} ticks",
    )

    # 4) AA Engagement — how much of its magazine the AA spent on us (lower=better)
    aa_eng = MetricResult(
        "aa_engagement", "AA Engagement",
        _round(_safe_div(ep.aa_ammo_used, ep.aa_max_ammo)),
        THRESHOLDS["aa_engagement"], "min", "",
        note=f"{ep.aa_ammo_used}/{ep.aa_max_ammo} rounds, peak track {ep.aa_tracked_peak}",
    )

    # 5) Broadcast Latency — ticks to first detection broadcast (lower=better)
    bl_value = float(ep.broadcast_step) if (ep.broadcast and ep.broadcast_step is not None) else None
    bl_note = (f"detected at tick {ep.broadcast_step}" if bl_value is not None
               else "target never broadcast")
    broadcast_lat = MetricResult(
        "broadcast_latency", "Broadcast Latency", bl_value,
        THRESHOLDS["broadcast_latency"], "min", "ticks", note=bl_note,
    )

    return [kill_eff, recon_surv, net_surv, aa_eng, broadcast_lat]


def llm_accuracy_metric(ok_rate: Optional[float], calls: int) -> MetricResult:
    """The sixth metric — built from the llm_audit.jsonl summary."""
    return MetricResult(
        "llm_accuracy", "LLM Accuracy", _round(ok_rate),
        THRESHOLDS["llm_accuracy"], "max", "",
        note=f"{calls} gateway calls" if calls else "no LLM calls logged",
    )


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 4)
