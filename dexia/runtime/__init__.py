"""Dexia Runtime — declarative deployment layer (Phase 10).

DexiaConfig (single-source-of-truth config) + HealthMonitor (tick-stall
watchdog). Pure stdlib + PyYAML, so it runs identically on a laptop and inside
the docker-compose stack.
"""

from .config import (
    DexiaConfig,
    DEFAULT_CONFIG_PATH,
    DEFAULTS,
    load_config,
    get_config,
)
from .health import HealthMonitor, DEFAULT_TELEMETRY_PATH

__all__ = [
    "DexiaConfig", "DEFAULT_CONFIG_PATH", "DEFAULTS", "load_config", "get_config",
    "HealthMonitor", "DEFAULT_TELEMETRY_PATH",
]
