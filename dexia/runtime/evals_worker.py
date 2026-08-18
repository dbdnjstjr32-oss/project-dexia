"""Evals worker — the ``dexia-evals`` service entrypoint (Phase 10).

A standalone loop that periodically scores the *current* mission (live telemetry
snapshot + the three audit trails) and appends the verdict to
``evals_results.jsonl``, which the HUD Evals panel reads. This keeps the
scorecard continuously fresh without the operator clicking "평가 실행".

Pure stdlib — no Ray. Runs in its own slim container; the heavy checkpoint
rollout stays in the eval_phase9.py CLI.

    python -m dexia.runtime.evals_worker --interval 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from ..evals import EpisodeEvalSuite, episode_from_telemetry
from .config import get_config

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TELEMETRY_PATH = os.path.join(_ROOT, "telemetry.json")


def _read_telemetry() -> dict | None:
    if not os.path.exists(TELEMETRY_PATH):
        return None
    try:
        with open(TELEMETRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def run(interval: float, once: bool = False, results_path: str | None = None) -> int:
    get_config().apply()  # pick up declarative thresholds
    suite = EpisodeEvalSuite(results_path) if results_path else EpisodeEvalSuite()
    print(f"[evals-worker] live mission eval every {interval}s -> {suite.results_path}",
          flush=True)
    while True:
        telem = _read_telemetry()
        if telem is None:
            print("[evals-worker] no telemetry yet — waiting for the streamer", flush=True)
        else:
            ep = episode_from_telemetry(telem)
            report = suite.evaluate(ep)
            print(f"[evals-worker] tick={ep.steps} verdict={report['verdict']} "
                  f"({report['passed']}/{report['evaluated']})", flush=True)
        if once:
            return 0
        time.sleep(max(1.0, interval))


def main() -> int:
    ap = argparse.ArgumentParser(description="Dexia live evals worker")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between evals")
    ap.add_argument("--once", action="store_true", help="score once and exit")
    args = ap.parse_args()
    try:
        return run(args.interval, once=args.once)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
