"""Auth — lightweight principal + clearance for the control plane (AIP MAC, scaled).

Palantir's mandatory access control, sized for a single-node demo: an API key
maps to a *principal* (name + clearance), and every governed Action records WHO
invoked it. The control endpoints declare a required clearance; the ActionBus
enforces it. Air-gapped by design — keys are local config, no identity provider.

Principals come from ``dexia.config.yaml``::

    auth:
      principals:
        OPERATOR_KEY: {name: operator-1, clearance: operator}
        COMMANDER_KEY: {name: commander, clearance: commander}

If no principals are configured, a single built-in ``commander`` key keeps the
zero-setup demo working (and is clearly flagged as the default).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..runtime.config import get_config

API_KEY_HEADER = "X-Dexia-Key"

# Zero-setup fallback so a fresh checkout still runs end-to-end. Documented as a
# default; real deployments override via dexia.config.yaml / env.
DEFAULT_PRINCIPALS = {
    "dexia-commander": {"name": "commander", "clearance": "commander"},
    "dexia-operator": {"name": "operator-1", "clearance": "operator"},
}


@dataclass(frozen=True)
class Principal:
    name: str
    clearance: str
    key: str


def _principal_table() -> dict:
    cfg = get_config()
    table = cfg.get("auth.principals", None)
    if not isinstance(table, dict) or not table:
        return DEFAULT_PRINCIPALS
    return table


def resolve_principal(api_key: Optional[str]) -> Optional[Principal]:
    """Resolve an API key to a Principal, or None if unknown/missing."""
    if not api_key:
        return None
    entry = _principal_table().get(api_key)
    if not isinstance(entry, dict):
        return None
    return Principal(
        name=str(entry.get("name", "unknown")),
        clearance=str(entry.get("clearance", "operator")),
        key=api_key,
    )
