"""LineageStore — SQLite-backed Action provenance + ontology history (AIP scale).

The persistence substrate that makes Dexia an AIP-faithful system at small scale:
every governed Action the ActionBus accepts/rejects is appended here as immutable
*lineage* (who · what · accepted/rejected · resulting command), and the streamer
periodically writes typed ontology *snapshots* so the object graph has queryable
history (per-drone trajectory, kill-chain timeline) instead of a single live frame.

Single file (``dexia.db`` at the project root), stdlib ``sqlite3`` in **WAL mode**
so the streamer process and the FastAPI process can read/write concurrently
without the file-lock dance the JSON command queue needs. No extra service.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB_PATH = os.path.join(_ROOT, "dexia.db")

# Serialize writes within a process; WAL handles cross-process concurrency.
_WRITE_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(db_path: str) -> None:
    if db_path in _INITIALIZED:
        return
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lineage (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                principal   TEXT,
                action      TEXT NOT NULL,
                agent_id    TEXT,
                payload     TEXT,
                status      TEXT NOT NULL,
                reason      TEXT,
                command_id  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_lineage_ts ON lineage(ts);
            CREATE INDEX IF NOT EXISTS idx_lineage_action ON lineage(action);

            CREATE TABLE IF NOT EXISTS ontology_snapshot (
                tick     INTEGER,
                ts       TEXT NOT NULL,
                snapshot TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_snapshot_tick ON ontology_snapshot(tick);
            """
        )
        conn.commit()
        _INITIALIZED.add(db_path)
    finally:
        conn.close()


class LineageStore:
    """Thin SQLite wrapper. One instance is fine to share; methods open their own
    connection so it is safe across uvicorn worker threads."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        _ensure_schema(db_path)

    # ---- writes ---------------------------------------------------------- #
    def append_lineage(self, *, principal: Optional[str], action: str,
                       agent_id: Optional[str], payload: Optional[dict],
                       status: str, reason: Optional[str] = None,
                       command_id: Optional[str] = None) -> int:
        """Append one immutable Action provenance row. Returns the lineage id."""
        with _WRITE_LOCK:
            conn = _connect(self.db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO lineage (ts, principal, action, agent_id, payload, "
                    "status, reason, command_id) VALUES (?,?,?,?,?,?,?,?)",
                    (_now(), principal, action, agent_id,
                     json.dumps(payload or {}), status, reason, command_id),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def write_snapshot(self, tick: int, snapshot: dict) -> None:
        """Persist a typed ontology snapshot (the streamer calls this every N ticks)."""
        with _WRITE_LOCK:
            conn = _connect(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO ontology_snapshot (tick, ts, snapshot) VALUES (?,?,?)",
                    (int(tick), _now(), json.dumps(snapshot)),
                )
                conn.commit()
            finally:
                conn.close()

    # ---- reads ----------------------------------------------------------- #
    def recent_lineage(self, limit: int = 50) -> list[dict]:
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM lineage ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
            return [self._lineage_row(r) for r in rows]
        finally:
            conn.close()

    def drone_history(self, agent_id: str, limit: int = 50) -> list[dict]:
        """Per-tick trajectory of one drone, reconstructed from snapshots."""
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT tick, ts, snapshot FROM ontology_snapshot ORDER BY rowid DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        finally:
            conn.close()
        history = []
        for r in rows:
            snap = json.loads(r["snapshot"])
            drone = next(
                (d for d in snap.get("objects", {}).get("DroneObject", [])
                 if d.get("agent_id") == agent_id),
                None,
            )
            if drone is not None:
                history.append({
                    "tick": r["tick"], "ts": r["ts"],
                    "position": drone.get("position"), "alt": drone.get("alt"),
                    "status": drone.get("status"), "snr_db": drone.get("snr_db"),
                })
        return history

    @staticmethod
    def _lineage_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except (ValueError, TypeError):
            d["payload"] = {}
        return d


# shared default instance
_STORE: LineageStore | None = None


def get_store(db_path: Optional[str] = None) -> LineageStore:
    """Shared store. ``db_path`` defaults to the module ``DEFAULT_DB_PATH`` at
    call time (so tests can repoint it), with ``DEXIA_DB_PATH`` env override."""
    global _STORE
    db_path = db_path or os.environ.get("DEXIA_DB_PATH") or DEFAULT_DB_PATH
    if _STORE is None or _STORE.db_path != db_path:
        _STORE = LineageStore(db_path)
    return _STORE
