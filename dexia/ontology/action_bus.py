"""ActionBus — Funnel role: the ENFORCED, permissioned, audited write gate.

Every state-changing action is submitted here first. Validation, clearance, MAC
and audit all derive from the single ActionType registry (``actions.py``) so the
rules the HUD *displays* are the exact rules that *execute* — closing the gap
where the approve path used to bypass governance.

On submit the bus enforces, in order:
  1. known Action Type            (registry lookup)
  2. clearance                    (principal clearance >= ActionType.required_clearance)
  3. ontology guards              (LOST drone, kill-chain MAC, required payload)
and on accept returns the concrete command to enqueue (the Action's side effect)
plus the lineage id. Every attempt — accepted or rejected — is appended to the
SQLite lineage trail (who · what · status · resulting command).

``bypass=True`` (training mode) logs only and always accepts, so the RLlib
learning loop is never blocked (per the AIP plan's risk mitigation).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from .actions import ACTION_REGISTRY, ActionRejected, clearance_ok, to_command
from .store import get_store

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_AUDIT_PATH = os.path.join(_ROOT, "action_audit.jsonl")


class ActionBus:
    def __init__(self, registry, audit_path: str = DEFAULT_AUDIT_PATH,
                 bypass: bool = False, store=None) -> None:
        self.registry = registry
        self.audit_path = audit_path
        self.bypass = bool(bypass)
        self._store = store  # lazily resolved so unit tests can run DB-free

    def _lineage(self):
        if self._store is None:
            self._store = get_store()
        return self._store

    # ------------------------------------------------------------------ #
    def submit(self, action_type: str, agent_id: Optional[str] = None,
               payload: Optional[dict] = None, *, principal: Optional[str] = None,
               clearance: Optional[str] = None, record_lineage: bool = True) -> dict:
        """Validate + (optionally) record an action.

        ``clearance`` — the submitting principal's clearance. If provided, the
        bus enforces the ActionType's required clearance (the FastAPI execution
        path). If None, the clearance gate is skipped (internal/display callers
        such as the assess pipeline), but ontology guards still run.
        ``record_lineage`` — append to the SQLite trail (False for display-only).
        """
        payload = payload or {}
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action_type,
            "agent_id": agent_id,
            "principal": principal,
            "payload": payload,
            "mode": "bypass" if self.bypass else "enforce",
        }
        try:
            if not self.bypass:
                self._validate(action_type, agent_id, payload, clearance)
            record["status"] = "accepted"
            record["command"] = to_command(action_type, payload)
        except ActionRejected as e:
            record["status"] = "rejected"
            record["reason"] = str(e)
            record["command"] = None

        if record_lineage:
            # SQLite is the source of truth: commit first.
            # Only write the JSONL audit trail after the DB succeeds so both
            # stores are always consistent (either both written or neither).
            try:
                record["lineage_id"] = self._lineage().append_lineage(
                    principal=principal, action=action_type, agent_id=agent_id,
                    payload=payload, status=record["status"],
                    reason=record.get("reason"),
                )
                self._audit(record)  # JSONL only on DB success
            except Exception as e:
                # DB write failure -> fail-closed. Reject action and prevent enqueueing command.
                record["status"] = "rejected"
                record["reason"] = f"lineage logging failed: {str(e)}"
                record["command"] = None
                record["lineage_id"] = None
                # Audits the failure to JSONL as rejected.
                try:
                    self._audit(record)
                except Exception:
                    pass
        else:
            # Display-only path: no DB write, JSONL is best-effort only.
            self._audit(record)
        return record

    # ------------------------------------------------------------------ #
    def _validate(self, action_type: str, agent_id: Optional[str],
                  payload: dict, clearance: Optional[str]) -> None:
        at = ACTION_REGISTRY.get(action_type)
        if at is None:
            raise ActionRejected(f"unknown action type '{action_type}'")
        # clearance gate (only when a principal clearance context is supplied)
        if clearance is not None and not clearance_ok(clearance, at.required_clearance):
            raise ActionRejected(
                f"clearance '{clearance}' insufficient for '{action_type}' "
                f"(requires '{at.required_clearance}')"
            )
        # ontology-aware guards (state / MAC / required payload)
        if at.validate is not None:
            at.validate(self.registry, agent_id, payload)

    # ------------------------------------------------------------------ #
    def _audit(self, record: dict) -> None:
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass
