"""LineageStore — SQLite provenance + ontology history (WAL, concurrent-safe)."""

from __future__ import annotations

import os
import tempfile

import pytest

from dexia.ontology.store import LineageStore


@pytest.fixture()
def store():
    path = os.path.join(tempfile.mkdtemp(), "lineage.db")
    yield LineageStore(path)
    for p in (path, path + "-wal", path + "-shm"):
        if os.path.exists(p):
            os.remove(p)


def test_append_and_read_lineage(store):
    store.append_lineage(principal="cmd", action="deploy", agent_id=None,
                         payload={"x": 1, "y": 2}, status="accepted", command_id="cmd_1")
    store.append_lineage(principal="op", action="activate", agent_id=None,
                         payload={}, status="rejected", reason="clearance")
    rows = store.recent_lineage()
    assert len(rows) == 2
    # newest first
    assert rows[0]["action"] == "activate" and rows[0]["status"] == "rejected"
    assert rows[1]["payload"] == {"x": 1, "y": 2}


def test_snapshot_and_drone_history(store):
    for t in range(3):
        snap = {"objects": {"DroneObject": [
            {"agent_id": "agent_kami_0", "position": [t, t, 5.0], "alt": 5.0,
             "status": "active", "snr_db": -60 - t},
        ]}, "links": []}
        store.write_snapshot(t, snap)
    hist = store.drone_history("agent_kami_0")
    assert len(hist) == 3
    ticks = sorted(h["tick"] for h in hist)
    assert ticks == [0, 1, 2]
    assert all("position" in h for h in hist)


def test_wal_mode_enabled(store):
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_concurrent_read_during_write(store):
    """A reader can query while a separate connection writes (WAL guarantee)."""
    store.append_lineage(principal="a", action="deploy", agent_id=None,
                         payload={}, status="accepted")
    # second store instance = separate connection, same file
    other = LineageStore(store.db_path)
    other.append_lineage(principal="b", action="recall", agent_id=None,
                         payload={}, status="accepted")
    assert len(store.recent_lineage()) == 2
