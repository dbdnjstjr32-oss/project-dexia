"""loop_cli — CLI entry-point for a single mission run (HUD Build #8).

Wraps ``MissionRunner`` and additionally emits ``world_snapshot.jsonl`` —
a per-cycle timeline log that the wargame HUD replay player reads to draw:

  * blue/red unit positions (ground truth, 3D)
  * LOS results (precomputed by Python ``clear_los``; HUD displays only)
  * terrain flag (drives LosOverlay / TrajectoryLayer visibility)

Each line of ``world_snapshot.jsonl`` corresponds 1-for-1 with the same
``cycle`` index in ``reasoning_trace.jsonl`` so the HUD joins them by index.

Usage::

    python -m dexia.agent.loop_cli --scenario ridge-los-p4
    python -m dexia.agent.loop_cli --scenario ua-east-armor-thrust-007 --out-dir /tmp

Only allowed Python addition for Build #8 — no core module is modified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from ..physics3d import clear_los
from ..scenario.catalog import get_catalog
from ..scenario.scenario import load_scenario
from .loop import DEFAULT_TRACE_PATH, MissionRunner

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SNAPSHOT_PATH = os.path.join(_ROOT, "world_snapshot.jsonl")


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _entity_dict(e) -> dict:
    """Serialise one Entity to a plain dict for the snapshot."""
    return {
        "id": e.entity_id,
        "cls": e.cls,
        "pos": [round(float(v), 2) for v in e.position],
        "category": e.category,
        "alive": e.alive,
    }


def _compute_los(world) -> list:
    """Compute line-of-sight for every (blue_sensor, red_unit) pair.

    Uses the same ``clear_los`` function that ``PlatformSensorFeed.observe``
    uses internally, so Python truth == HUD display.  On flat scenarios
    (no terrain) returns an empty list and the HUD hides the LOS overlay.
    """
    terrain = getattr(world, "terrain", None)
    if terrain is None:
        return []

    los_records = []
    for blue in world.blue:
        spec = world.catalog.get(blue.cls)
        if spec is None:
            continue
        # Only sensor platforms produce meaningful LOS lines
        has_sensor = (
            spec.provides_sensor("eo_ir") is not None
            or spec.provides_sensor("radar") is not None
            or spec.provides_sensor("acoustic") is not None
        )
        if not has_sensor:
            continue
        for red in world.entities:
            if red.side != "red":
                continue
            visible = clear_los(terrain, blue.position, red.position)
            los_records.append({
                "observer": blue.entity_id,
                "target": red.entity_id,
                "visible": visible,
                "blocked_by_terrain": not visible,
            })
    return los_records


# ---------------------------------------------------------------------------
# Instrumented MissionRunner subclass
# ---------------------------------------------------------------------------

class SnapshotRunner(MissionRunner):
    """MissionRunner that additionally writes ``world_snapshot.jsonl``."""

    def __init__(self, *args, snapshot_path: Optional[str] = DEFAULT_SNAPSHOT_PATH,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.snapshot_path = snapshot_path
        if snapshot_path:
            try:
                open(snapshot_path, "w", encoding="utf-8").close()
            except Exception:
                self.snapshot_path = None

    def _emit(self, rec) -> None:
        """Override: write trace record then matching snapshot record."""
        super()._emit(rec)
        if not self.snapshot_path:
            return
        snapshot = {
            "cycle": rec.cycle,
            "tick": rec.tick,
            "has_terrain": self.world.terrain is not None,
            "blue": [_entity_dict(e) for e in self.world.blue],
            "red": [_entity_dict(e) for e in self.world.entities
                    if e.side == "red"],
            "los": _compute_los(self.world),
        }
        try:
            with open(self.snapshot_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="Run one Dexia scenario and write reasoning_trace.jsonl + world_snapshot.jsonl."
    )
    ap.add_argument("--scenario", required=True,
                    help="Scenario id (e.g. ridge-los-p4) or path to .yaml file")
    ap.add_argument("--out-dir", default=_ROOT,
                    help="Directory to write output files (default: repo root)")
    ap.add_argument("--max-cycles", type=int, default=16)
    args = ap.parse_args(argv)

    # Resolve scenario
    sc_arg = args.scenario
    if not sc_arg.endswith(".yaml"):
        # Try known locations: scenarios/<id>.yaml or scenarios/generated/<id>.yaml
        for candidate in [
            os.path.join(_ROOT, "scenarios", f"{sc_arg}.yaml"),
            os.path.join(_ROOT, "scenarios", "generated", f"{sc_arg}.yaml"),
        ]:
            if os.path.exists(candidate):
                sc_arg = candidate
                break

    sc = load_scenario(sc_arg, validate=False)
    catalog = get_catalog()

    trace_path = os.path.join(args.out_dir, "reasoning_trace.jsonl")
    snapshot_path = os.path.join(args.out_dir, "world_snapshot.jsonl")

    print(f"[loop_cli] 시나리오: {sc.id}  theater: {sc.theater}")
    print(f"[loop_cli] trace    → {trace_path}")
    print(f"[loop_cli] snapshot → {snapshot_path}")

    runner = SnapshotRunner(
        sc, catalog,
        max_cycles=args.max_cycles,
        trace_path=trace_path,
        snapshot_path=snapshot_path,
    )
    result = runner.run()

    print(f"[loop_cli] 완료: outcome={result['outcome']} "
          f"cycles={result['cycles']} trace_len={result['trace_len']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
