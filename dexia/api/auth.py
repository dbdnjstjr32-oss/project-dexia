"""Auth — principal + clearance for the control plane (AIP MAC, scaled).

Palantir's mandatory access control, sized for a single-node demo: an API key
maps to a *principal* (name + clearance), and every governed Action records WHO
invoked it. The control endpoints declare a required clearance; the ActionBus
enforces it.

Key resolution (highest precedence first):
  1. Environment keys — ``DEXIA_COMMANDER_KEY`` / ``DEXIA_OPERATOR_KEY``.
     Set these in any real deployment (docker-compose / systemd / CI).
  2. ``auth.principals`` in ``dexia.config.yaml`` — an explicit, operator-owned
     key→principal table.
  3. Built-in DEV defaults (``dexia-commander`` / ``dexia-operator``) — these are
     PUBLIC constants, so they are accepted ONLY when ``DEXIA_ALLOW_DEFAULT_KEYS``
     is truthy (local dev / CI opt-in). A loud warning is emitted when used.
  4. Otherwise the table is EMPTY and every key is rejected (fail closed) — an
     unconfigured deployment cannot be driven by a guessed header.

Security note: the previous build shipped the well-known keys both here and in
``dexia.config.yaml``, so anyone reaching the port could send
``X-Dexia-Key: dexia-commander`` and obtain commander clearance. The defaults are
now gated behind an explicit dev flag and the config no longer ships working
credentials, so the funnel fails closed by default.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from ..runtime.config import get_config

API_KEY_HEADER = "X-Dexia-Key"

_log = logging.getLogger(__name__)

# Built-in DEV-ONLY keys. PUBLIC constants — accepted only when
# DEXIA_ALLOW_DEFAULT_KEYS is enabled. Never rely on these in a real deployment.
DEFAULT_PRINCIPALS = {
    "dexia-commander": {"name": "commander", "clearance": "commander"},
    "dexia-operator": {"name": "operator-1", "clearance": "operator"},
}

_TRUTHY = {"1", "true", "yes", "on"}
# one-shot warning latches so the logs aren't spammed every request
_warned = {"dev": False, "closed": False}


@dataclass(frozen=True)
class Principal:
    name: str
    clearance: str
    key: str


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def _env_principals() -> dict:
    """Key→principal entries defined via environment variables (precedence 1)."""
    table: dict = {}
    ck = os.environ.get("DEXIA_COMMANDER_KEY")
    if ck:
        table[ck] = {"name": "commander", "clearance": "commander"}
    ok = os.environ.get("DEXIA_OPERATOR_KEY")
    if ok:
        table[ok] = {"name": "operator-1", "clearance": "operator"}
    return table


def _principal_table() -> dict:
    # 1. explicit environment keys (real deployments / CI)
    env_table = _env_principals()
    if env_table:
        return env_table
    # 2. operator-owned config table (dexia.config.yaml: auth.principals)
    cfg_table = get_config().get("auth.principals", None)
    if isinstance(cfg_table, dict) and cfg_table:
        return cfg_table
    # 3. built-in DEV defaults — explicit opt-in only, loudly warned
    if _truthy_env("DEXIA_ALLOW_DEFAULT_KEYS"):
        if not _warned["dev"]:
            _log.warning(
                "AUTH: accepting built-in DEFAULT keys because "
                "DEXIA_ALLOW_DEFAULT_KEYS is set. These are PUBLIC constants — "
                "use this for local dev only. Set DEXIA_COMMANDER_KEY/"
                "DEXIA_OPERATOR_KEY or auth.principals for any real deployment."
            )
            _warned["dev"] = True
        return dict(DEFAULT_PRINCIPALS)
    # 4. fail closed — no principals configured, every governed write is 401
    if not _warned["closed"]:
        _log.warning(
            "AUTH: no principals configured (no DEXIA_*_KEY env, no "
            "auth.principals in config). All governed writes will be rejected "
            "(401). Set keys, or DEXIA_ALLOW_DEFAULT_KEYS=1 for local dev."
        )
        _warned["closed"] = True
    return {}


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
