"""DexiaConfig — declarative runtime configuration (Phase 10).

Loads ``dexia.config.yaml`` (the single source of truth), merges it over
built-in defaults, and applies environment-variable overrides so docker-compose
can inject per-service settings. ``.apply()`` pushes the declarative knobs into
the live modules they govern (currently the EpisodeEvalSuite thresholds).

PyYAML is the only dependency; if the file is missing the defaults still yield a
fully working single-host config (zero-setup parity with the rest of Dexia).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG_PATH = os.path.join(_ROOT, "dexia.config.yaml")

DEFAULTS: dict[str, Any] = {
    "runtime": {"scenario": "interactive", "hz": 10.0, "airgap": False},
    "sim": {"enable_aa": True, "num_recon": 2, "num_kami": 4},
    "llm": {
        "provider": "ollama",
        "tactical_model": "llama3.1:8b-instruct-q4_K_M",
        "summary_model": "qwen2.5:7b",
        "host": None,
    },
    "evals": {"thresholds": {
        "kill_efficiency": 0.50, "recon_survival": 0.50, "net_survivability": 0.70,
        "aa_engagement": 0.50, "broadcast_latency": 250, "llm_accuracy": 0.90,
    }},
    "redis": {"enabled": True, "host": "127.0.0.1", "port": 6379},
    "health": {"stall_seconds": 5.0, "restart_on_stall": True},
    "services": {"api_port": 8000, "hud_port": 3000},
}

# ENV var -> (dotted path, caster). Lets compose/CI override any field.
ENV_OVERRIDES: dict[str, tuple[str, Any]] = {
    "DEXIA_SCENARIO": ("runtime.scenario", str),
    "DEXIA_HZ": ("runtime.hz", float),
    "DEXIA_AIRGAP": ("runtime.airgap", lambda v: str(v).lower() in ("1", "true", "yes")),
    "DEXIA_LLM_PROVIDER": ("llm.provider", str),
    "OLLAMA_HOST": ("llm.host", str),
    "REDIS_HOST": ("redis.host", str),
    "REDIS_PORT": ("redis.port", int),
    "DEXIA_REDIS_ENABLED": ("redis.enabled", lambda v: str(v).lower() in ("1", "true", "yes")),
    "DEXIA_STALL_SECONDS": ("health.stall_seconds", float),
    "DEXIA_API_PORT": ("services.api_port", int),
    "DEXIA_HUD_PORT": ("services.hud_port", int),
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _set_path(d: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


@dataclass
class DexiaConfig:
    data: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    source: Optional[str] = None

    # ---- typed accessors --------------------------------------------- #
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def scenario(self) -> str: return self.get("runtime.scenario", "interactive")
    @property
    def hz(self) -> float: return float(self.get("runtime.hz", 10.0))
    @property
    def airgap(self) -> bool: return bool(self.get("runtime.airgap", False))
    @property
    def llm_provider(self) -> str:
        # air-gap forces local regardless of the declared provider
        return "ollama" if self.airgap else self.get("llm.provider", "ollama")
    @property
    def redis_enabled(self) -> bool: return bool(self.get("redis.enabled", True))
    @property
    def stall_seconds(self) -> float: return float(self.get("health.stall_seconds", 5.0))
    @property
    def thresholds(self) -> dict: return dict(self.get("evals.thresholds", {}) or {})

    # ---- side effects ------------------------------------------------ #
    def apply(self) -> "DexiaConfig":
        """Push declarative settings into the modules they govern."""
        try:
            from ..evals import metrics as _m
            _m.THRESHOLDS.update({k: v for k, v in self.thresholds.items() if k in _m.THRESHOLDS})
        except Exception:
            pass
        return self

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)


def load_config(path: Optional[str] = None, *, apply: bool = False) -> DexiaConfig:
    """Defaults <- yaml file <- environment overrides."""
    path = path or DEFAULT_CONFIG_PATH
    merged = copy.deepcopy(DEFAULTS)
    source = None
    if yaml is not None and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, loaded)
            source = path
        except Exception:
            pass

    for env, (dotted, cast) in ENV_OVERRIDES.items():
        if env in os.environ and os.environ[env] != "":
            try:
                _set_path(merged, dotted, cast(os.environ[env]))
            except Exception:
                pass

    cfg = DexiaConfig(data=merged, source=source)
    return cfg.apply() if apply else cfg


# process-wide shared config
_CONFIG: Optional[DexiaConfig] = None


def get_config() -> DexiaConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG
