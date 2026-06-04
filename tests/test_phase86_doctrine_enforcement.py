"""Phase 8.6 verification — doctrine-driven physics enforcement.

Proves the MARL environment physically respects the AI-learned Tactical Recipe:
with doctrine enforcement ON (min_scatter_distance_m=50), active kamikazes are
pushed apart by an artificial repulsive force and end up markedly more dispersed
than with enforcement OFF — and no drone is lost in the process (the dispersion
is stable, horizontal-only).

Dual-mode: ``pytest tests/test_phase86_doctrine_enforcement.py`` and direct
(``python tests/test_phase86_doctrine_enforcement.py``).
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings
warnings.filterwarnings("ignore")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import numpy as np

from dexia.envs.drone_marl_env import DroneMARLEnv

STEPS = 150
SEED = 7


def _recipe_file(tmpdir: str, version: float, min_scatter: float) -> str:
    path = os.path.join(tmpdir, f"recipes_v{version}_s{min_scatter}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"scenario": "SEAD", "version": version,
                   "rules": {"min_scatter_distance_m": min_scatter,
                             "max_altitude_m": 50, "target_priority": "AA_RADAR"}}, f)
    return path


def _mean_kami_spacing(env) -> float:
    pos = [env.engines[a].get_state().position[:2] for a in env.combat_kami_ids]
    pairs = list(itertools.combinations(pos, 2))
    return float(np.mean([np.linalg.norm(p - q) for p, q in pairs])) if pairs else 0.0


def _rollout(recipes_path: str, enforce: bool, steps: int = STEPS) -> dict:
    env = DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "pool_size": 0, "seed": SEED,
        "enable_wind": False, "enable_baro": False, "enable_sensor_noise": False,
        "enable_aa": False, "armed": True,
        "enforce_doctrine": enforce, "recipes_path": recipes_path,
    })
    env.reset(seed=SEED)
    start = _mean_kami_spacing(env)
    for _ in range(steps):
        actions = {a: env.engines[a].hover_action for a in env.possible_agents}
        env.step(actions)
    return {
        "start_spacing": start,
        "end_spacing": _mean_kami_spacing(env),
        "lost": len(env.get_full_state()["lost"]),
        "doctrine_version": env.current_doctrine_version,
        "min_scatter": env.current_doctrine.get("min_scatter_distance_m"),
    }


# --------------------------------------------------------------------------- #
def test_doctrine_loaded_into_env():
    with tempfile.TemporaryDirectory() as d:
        path = _recipe_file(d, 1.1, 50)
        env = DroneMARLEnv({"num_kami": 4, "seed": SEED, "recipes_path": path})
        env.reset(seed=SEED)
        assert env.current_doctrine_version == 1.1
        assert env.current_doctrine["min_scatter_distance_m"] == 50
        # telemetry reflection: surfaced in get_full_state()
        assert env.get_full_state()["current_doctrine_version"] == 1.1


def test_enforcement_disperses_kamikazes():
    with tempfile.TemporaryDirectory() as d:
        path = _recipe_file(d, 1.1, 50)
        off = _rollout(path, enforce=False)
        on = _rollout(path, enforce=True)

    # ON pushes the swarm markedly wider than OFF (the learned 50m doctrine)
    assert on["end_spacing"] > off["end_spacing"] * 1.5, (off, on)
    # the dispersion is also wider than where it started
    assert on["end_spacing"] > on["start_spacing"]
    # ...and it is stable — horizontal-only repulsion crashes nobody
    assert on["lost"] == 0 and off["lost"] == 0


def test_disabled_enforcement_is_inert():
    """enforce_doctrine=False must leave the original dynamics untouched."""
    with tempfile.TemporaryDirectory() as d:
        path = _recipe_file(d, 1.1, 50)
        off = _rollout(path, enforce=False)
    # without enforcement the swarm stays near its initial (tight) formation
    assert abs(off["end_spacing"] - off["start_spacing"]) < 3.0


# --------------------------------------------------------------------------- #
def main() -> int:
    bar = "=" * 74
    print(bar)
    print("DEXIA Phase 8.6 — Doctrine-driven physics enforcement (SEAD scatter)")
    print(bar)

    with tempfile.TemporaryDirectory() as d:
        path_v1 = _recipe_file(d, 1.0, 10)
        path_v11 = _recipe_file(d, 1.1, 50)
        off = _rollout(path_v11, enforce=False)
        on_v1 = _rollout(path_v1, enforce=True)
        on_v11 = _rollout(path_v11, enforce=True)

    print(f"\n  combat kami = 4 · {STEPS} steps · hover · no wind/AA")
    print(f"  initial mean spacing: {off['start_spacing']:.2f} m\n")
    print(f"  {'config':<34}{'doctrine':<12}{'end spacing':<14}{'lost'}")
    print("  " + "-" * 66)
    print(f"  {'enforcement OFF':<34}{'(inert)':<12}{off['end_spacing']:<14.2f}{off['lost']}")
    print(f"  {'ON — recipe v1.0 (min_scatter=10)':<34}"
          f"{'v'+str(on_v1['doctrine_version']):<12}{on_v1['end_spacing']:<14.2f}{on_v1['lost']}")
    print(f"  {'ON — recipe v1.1 (min_scatter=50)':<34}"
          f"{'v'+str(on_v11['doctrine_version']):<12}{on_v11['end_spacing']:<14.2f}{on_v11['lost']}")

    ok = (on_v11["end_spacing"] > off["end_spacing"] * 1.5
          and on_v11["end_spacing"] > on_v1["end_spacing"]
          and on_v11["lost"] == 0)
    print("\n" + bar)
    print(f"DOCTRINE ENFORCEMENT {'VERIFIED ✅' if ok else 'FAILED ❌'}  "
          f"(v1.1 disperses {on_v11['end_spacing']/max(off['end_spacing'],1e-6):.1f}× wider "
          f"than uncontrolled, 0 losses)")
    print(bar)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
