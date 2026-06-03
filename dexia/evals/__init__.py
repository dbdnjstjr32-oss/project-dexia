"""Dexia Evals — Phase 9 evaluation + observability layer.

EpisodeEvalSuite scores an episode against six thresholded AIP metrics and folds
in the three immutable audit trails (action / llm / ontology). Pure stdlib so it
runs on either interpreter, in CI, and on the air-gapped evals service.
"""

from .metrics import (
    EpisodeRecord,
    MetricResult,
    THRESHOLDS,
    evaluate_episode,
    llm_accuracy_metric,
)
from .audit import (
    observability_summary,
    summarize_action_audit,
    summarize_llm_audit,
    summarize_ontology_state,
    read_jsonl,
)
from .suite import (
    EpisodeEvalSuite,
    DEFAULT_RESULTS_PATH,
    episode_from_telemetry,
)

__all__ = [
    "EpisodeRecord", "MetricResult", "THRESHOLDS",
    "evaluate_episode", "llm_accuracy_metric",
    "observability_summary", "summarize_action_audit", "summarize_llm_audit",
    "summarize_ontology_state", "read_jsonl",
    "EpisodeEvalSuite", "DEFAULT_RESULTS_PATH", "episode_from_telemetry",
]
