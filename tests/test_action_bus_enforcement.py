"""ActionBus is the ENFORCED write gate — clearance, MAC, state, and lineage.

These assert the rules the HUD displays are the rules that execute, and that
every attempt (accepted or rejected) lands in the immutable SQLite lineage trail.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from dexia.ontology import InMemoryRegistry
from dexia.ontology.action_bus import ActionBus
from dexia.ontology.schema import DroneObject, MissionObject
from dexia.ontology.store import LineageStore


@pytest.fixture()
def store():
    path = os.path.join(tempfile.mkdtemp(), "lineage.db")
    yield LineageStore(path)
    for p in (path, path + "-wal", path + "-shm"):
        if os.path.exists(p):
            os.remove(p)


@pytest.fixture()
def registry():
    reg = InMemoryRegistry()
    reg.replace("DroneObject", [
        DroneObject(agent_id="agent_kami_0", kind="kami", status="active"),
        DroneObject(agent_id="agent_recon_0", kind="recon", status="lost"),
    ])
    reg.replace("MissionObject", [MissionObject(broadcast=False)])
    return reg


def test_clearance_reject(registry, store):
    bus = ActionBus(registry, store=store)
    r = bus.submit("activate", payload={}, principal="op", clearance="operator")
    assert r["status"] == "rejected"
    assert "clearance" in r["reason"]


def test_clearance_accept(registry, store):
    bus = ActionBus(registry, store=store)
    r = bus.submit("activate", payload={}, principal="cmd", clearance="commander")
    assert r["status"] == "accepted"
    assert r["command"] == {"action": "arm"}


def test_killchain_mac_reject(registry, store):
    """A kamikaze cannot engage before the recon broadcast."""
    bus = ActionBus(registry, store=store)
    r = bus.submit("engage", agent_id="agent_kami_0", payload={},
                   principal="cmd", clearance="commander")
    assert r["status"] == "rejected"
    assert "broadcast" in r["reason"]


def test_killchain_mac_allows_after_broadcast(registry, store):
    registry.replace("MissionObject", [MissionObject(broadcast=True)])
    bus = ActionBus(registry, store=store)
    r = bus.submit("engage", agent_id="agent_kami_0", payload={},
                   principal="cmd", clearance="commander")
    assert r["status"] == "accepted"


def test_lost_drone_reject(registry, store):
    bus = ActionBus(registry, store=store)
    r = bus.submit("move", agent_id="agent_recon_0", payload={"x": 1, "y": 2},
                   principal="op", clearance="operator")
    assert r["status"] == "rejected"
    assert "LOST" in r["reason"]


def test_missing_payload_reject(registry, store):
    bus = ActionBus(registry, store=store)
    r = bus.submit("deploy", payload={}, principal="op", clearance="operator")
    assert r["status"] == "rejected"


def test_accept_writes_lineage(registry, store):
    bus = ActionBus(registry, store=store)
    bus.submit("deploy", payload={"x": 5, "y": 6}, principal="op-1", clearance="operator")
    rows = store.recent_lineage()
    assert len(rows) == 1
    assert rows[0]["principal"] == "op-1"
    assert rows[0]["action"] == "deploy"
    assert rows[0]["status"] == "accepted"


def test_reject_also_writes_lineage(registry, store):
    bus = ActionBus(registry, store=store)
    bus.submit("activate", payload={}, principal="op-1", clearance="operator")
    rows = store.recent_lineage()
    assert rows[0]["status"] == "rejected"
    assert rows[0]["reason"]


def test_display_path_no_lineage(registry, store):
    """The assess/display preview must not pollute the execution lineage."""
    bus = ActionBus(registry, store=store)
    bus.submit("deploy", payload={"x": 1, "y": 2}, principal="system-aip",
               record_lineage=False)
    assert store.recent_lineage() == []
