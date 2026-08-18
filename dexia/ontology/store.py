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
# Guard the one-time schema init and the shared-instance singleton separately so
# two uvicorn worker threads can't race them (and so they never deadlock: a
# get_store() under _STORE_LOCK calls _ensure_schema() which takes _INIT_LOCK).
_INIT_LOCK = threading.Lock()
_STORE_LOCK = threading.Lock()
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
    with _INIT_LOCK:
        if db_path in _INITIALIZED:        # double-checked under the lock
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
        """Per-tick trajectory of one drone, reconstructed from snapshots.

        [결함 16] 최적화: SQLite json_extract() 를 사용하여 DB 내에서 직접
        DroneObject 배열을 탐색. 전체 snapshot JSON blob 을 Python 으로 불러온 뒤
        루프로 파싱하는 병목을 제거한다.

        SQLite 3.38+ (2022-02-22 이후 배포판)에 json_each() 가 내장되어 있다.
        구버전 sqlite3 에는 json_each 가 없을 수 있으므로 fallback 경로도 유지한다.
        """
        conn = _connect(self.db_path)
        try:
            # --- 빠른 경로: json_each() 지원 여부 확인 -------------------
            try:
                rows = conn.execute(
                    """
                    SELECT
                        s.tick,
                        s.ts,
                        json_extract(obj.value, '$.position') AS position,
                        json_extract(obj.value, '$.alt')      AS alt,
                        json_extract(obj.value, '$.status')   AS status,
                        json_extract(obj.value, '$.snr_db')   AS snr_db
                    FROM (
                        SELECT tick, ts, snapshot
                        FROM ontology_snapshot
                        ORDER BY rowid DESC
                        LIMIT ?
                    ) AS s,
                    json_each(
                        json_extract(s.snapshot, '$.objects.DroneObject')
                    ) AS obj
                    WHERE json_extract(obj.value, '$.agent_id') = ?
                    """,
                    (max(1, min(limit, 500)), agent_id),
                ).fetchall()
                return [
                    {
                        "tick": r["tick"],
                        "ts": r["ts"],
                        "position": json.loads(r["position"]) if isinstance(r["position"], str) else r["position"],
                        "alt": r["alt"],
                        "status": r["status"],
                        "snr_db": r["snr_db"],
                    }
                    for r in rows
                ]
            except Exception:
                # --- fallback: 구버전 SQLite — 전체 blob 로드 후 Python 필터 ---
                rows = conn.execute(
                    "SELECT tick, ts, snapshot FROM ontology_snapshot ORDER BY rowid DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                ).fetchall()
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
        finally:
            conn.close()

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
    store = _STORE
    if store is not None and store.db_path == db_path:
        return store
    with _STORE_LOCK:                       # serialize singleton (re)creation
        if _STORE is None or _STORE.db_path != db_path:
            _STORE = LineageStore(db_path)
        return _STORE
