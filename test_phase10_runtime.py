"""Phase 10 verification — DexiaRuntime (config + HealthMonitor + compose).

No docker: assert the declarative config loader (defaults <- yaml <- env), the
threshold-apply side effect, the HealthMonitor stall logic (with an injected
clock, no real sleeps), and the docker-compose.yml shape (5 services, single
bring-up). The evals worker is exercised once against a temp telemetry snapshot.

Run (Python 3.12 venv):
    .venv312\\Scripts\\python.exe test_phase10_runtime.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import yaml

from dexia.runtime import DexiaConfig, DEFAULT_CONFIG_PATH, load_config
from dexia.runtime.config import DEFAULTS
from dexia.runtime.health import HealthMonitor

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _write_telem(path: str, tick: int, when: datetime | None) -> None:
    rec = {"tick": tick}
    if when is not None:
        rec["time"] = when.isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f)


def main() -> int:
    print("=" * 74)
    print("PHASE 10 — DexiaRuntime (config · HealthMonitor · compose)")
    print("=" * 74)

    # ---- 1) config: defaults <- yaml <- env -------------------------- #
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert isinstance(cfg, DexiaConfig) and cfg.source == DEFAULT_CONFIG_PATH
    assert cfg.scenario == "interactive" and cfg.hz == 10.0
    assert set(cfg.thresholds) == set(DEFAULTS["evals"]["thresholds"])

    # missing file -> pure defaults, still valid
    d = load_config(os.path.join(tempfile.gettempdir(), "nope.yaml"))
    assert d.source is None and d.hz == 10.0

    # env override wins, and airgap forces the local provider
    os.environ["DEXIA_HZ"] = "20"
    os.environ["DEXIA_AIRGAP"] = "true"
    os.environ["DEXIA_LLM_PROVIDER"] = "anthropic"
    try:
        e = load_config(DEFAULT_CONFIG_PATH)
        assert e.hz == 20.0, e.hz
        assert e.airgap is True
        assert e.llm_provider == "ollama", "airgap must force local LLM"
    finally:
        for k in ("DEXIA_HZ", "DEXIA_AIRGAP", "DEXIA_LLM_PROVIDER"):
            os.environ.pop(k, None)
    print("[1] config 로더(기본<-yaml<-env) . OK (env override + airgap=local 강제)")

    # ---- 2) apply() pushes thresholds into the evals layer ----------- #
    from dexia.evals import metrics as M
    saved = dict(M.THRESHOLDS)
    try:
        custom = DexiaConfig(data={"evals": {"thresholds": {"llm_accuracy": 0.42}}})
        custom.apply()
        assert M.THRESHOLDS["llm_accuracy"] == 0.42
    finally:
        M.THRESHOLDS.clear()
        M.THRESHOLDS.update(saved)
    print("[2] thresholds apply() ......... OK (config -> evals.metrics.THRESHOLDS)")

    # ---- 3) HealthMonitor stall logic (injected clock) --------------- #
    with tempfile.TemporaryDirectory() as td:
        tp = os.path.join(td, "telemetry.json")
        now = [1000.0]
        mon = HealthMonitor(tp, stall_seconds=5.0, clock=lambda: now[0])

        assert mon.sim_status()["state"] == "absent"          # no file yet

        # fresh snapshot (emitted "now") -> live
        _write_telem(tp, 1, datetime.fromtimestamp(now[0], tz=timezone.utc))
        s = mon.sim_status()
        assert s["state"] == "live" and not s["stalled"] and s["tick"] == 1

        # same tick, clock jumps 100s past the emit time -> stalled
        now[0] = 1100.0
        s = mon.sim_status()
        assert s["stalled"] and s["state"] == "stalled", s

        # advance the tick (no time field) -> resets the freshness window
        now[0] = 1100.0
        _write_telem(tp, 2, None)
        assert not mon.sim_status()["stalled"]
        now[0] = 1101.0                                        # 1s later, < 5s
        assert not mon.stalled()
        now[0] = 1110.0                                        # 9s, tick frozen
        assert mon.stalled()

        # whole-stack rollup with a service heartbeat
        mon.beat("dexia-api", ok=True)
        rep = mon.report()
        assert set(rep) == {"ok", "ts", "sim", "services"}
        assert "dexia-api" in rep["services"]
    print("[3] HealthMonitor 틱 정체 감지 .. OK (absent/live/stalled + rollup)")

    # ---- 4) docker-compose shape (single-command 5-service stack) ---- #
    with open(os.path.join(_ROOT, "docker-compose.yml"), "r", encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    svc = compose["services"]
    expected = {"redis", "dexia-sim", "dexia-api", "dexia-evals", "dexia-hud"}
    assert expected <= set(svc), set(svc)
    assert svc["dexia-api"]["ports"] == ["8000:8000"]
    assert svc["dexia-hud"]["environment"]  # SIM_API_URL wiring present
    assert svc["dexia-sim"]["restart"] == "unless-stopped"  # bounce a wedged streamer
    for name in ("docker/Dockerfile.sim", "docker/Dockerfile.api",
                 "docker/Dockerfile.evals", "dexia-hud/Dockerfile", "dexia.config.yaml"):
        assert os.path.exists(os.path.join(_ROOT, name)), name
    print(f"[4] docker-compose 5서비스 ..... OK ({', '.join(sorted(svc))})")

    # ---- 5) evals worker runs once against a snapshot ---------------- #
    from dexia.runtime import evals_worker
    with tempfile.TemporaryDirectory() as td:
        tp = os.path.join(td, "telemetry.json")
        _write_telem_full(tp)
        evals_worker.TELEMETRY_PATH = tp
        rc = evals_worker.run(interval=1, once=True,
                              results_path=os.path.join(td, "evals_results.jsonl"))
        assert rc == 0
    print("[5] evals 워커(--once) ......... OK (라이브 스냅샷 1회 채점)")

    print("\n" + "=" * 74)
    print("PHASE 10: config=P apply=P health=P compose=P worker=P  -> PASS")
    print("=" * 74)
    return 0


def _write_telem_full(path: str) -> None:
    rec = {
        "tick": 50,
        "time": datetime.now(timezone.utc).isoformat(),
        "agents": [{"kind": "recon", "lost": False}, {"kind": "kami", "lost": False}],
        "events": {"broadcast": True, "kill_confirmed": False, "total_lost": 0},
        "network_survivability": 0.9,
        "aa": {"ammo": 22, "max_ammo": 24, "tracked": []},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f)


if __name__ == "__main__":
    raise SystemExit(main())
