"""ActionBus — Funnel role: governed write validation + audit trail.

Every state-changing action is submitted here first. The bus enforces:
  * schema validation   — known action type, required payload props
  * state constraints    — a LOST drone cannot move/engage/deploy
  * MAC (kill-chain)     — a kamikaze cannot 'engage' before the broadcast
  * audit log            — every attempt (accepted/rejected) -> action_audit.jsonl

``bypass=True`` (training mode) logs only and always accepts, so the RLlib
learning loop is never blocked (per the AIP plan's risk mitigation).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from .schema import ACTION_TYPES

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_AUDIT_PATH = os.path.join(_ROOT, "action_audit.jsonl")


class ActionRejected(Exception):
    pass


class ActionBus:
    def __init__(self, registry, audit_path: str = DEFAULT_AUDIT_PATH, bypass: bool = False) -> None:
        self.registry = registry
        self.audit_path = audit_path
        self.bypass = bool(bypass)

    # ------------------------------------------------------------------ #
    def submit(self, action_type: str, agent_id: Optional[str] = None,
               payload: Optional[dict] = None) -> dict:
        payload = payload or {}
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action_type,
            "agent_id": agent_id,
            "payload": payload,
            "mode": "bypass" if self.bypass else "enforce",
        }
        try:
            if not self.bypass:
                self._validate(action_type, agent_id, payload)
            record["status"] = "accepted"
        except ActionRejected as e:
            record["status"] = "rejected"
            record["reason"] = str(e)
        self._audit(record)
        return record

    # ------------------------------------------------------------------ #
    def _validate(self, action_type: str, agent_id: Optional[str], payload: dict) -> None:
        if action_type not in ACTION_TYPES:
            raise ActionRejected(f"unknown action type '{action_type}'")

        if agent_id:
            drone = self.registry.get("DroneObject", agent_id)
            if drone is not None:
                # state constraint
                if drone.status == "lost" and action_type in ("move", "engage", "deploy"):
                    raise ActionRejected(f"agent {agent_id} is LOST — '{action_type}' denied")
                # MAC: kamikaze cannot engage before the kill-chain broadcast
                if drone.kind == "kami" and action_type == "engage":
                    missions = self.registry.all("MissionObject")
                    broadcast = missions[0].broadcast if missions else False
                    if not broadcast:
                        raise ActionRejected(
                            "kamikaze 'engage' denied before broadcast (kill-chain MAC)"
                        )

        # schema: required payload props
        if action_type in ("move", "deploy") and not ({"x", "y"} <= set(payload)):
            raise ActionRejected(f"'{action_type}' requires payload x, y")

    # ------------------------------------------------------------------ #
    def _audit(self, record: dict) -> None:
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass
