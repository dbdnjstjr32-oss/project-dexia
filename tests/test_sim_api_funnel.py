"""FastAPI control plane is the single GOVERNED write-funnel (end-to-end).

TestClient asserts the AIP enforcement contract over HTTP:
  * no/invalid key            -> 401
  * insufficient clearance    -> 403
  * ontology guard rejection  -> 409 (+ reason, + lineage row)
  * accepted action           -> 202 (+ lineage_id, + queued command)
and that every attempt is stamped into the SQLite lineage trail.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# isolate the lineage DB before the app/store import binds anything
os.environ["DEXIA_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "funnel.db")

from fastapi.testclient import TestClient  # noqa: E402

import dexia.ontology.store as store_mod  # noqa: E402
from dexia.api import sim_api  # noqa: E402
from dexia.ontology import InMemoryRegistry  # noqa: E402
from dexia.ontology.schema import DroneObject  # noqa: E402

OPERATOR = {"X-Dexia-Key": "dexia-operator"}
COMMANDER = {"X-Dexia-Key": "dexia-commander"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # fresh DB per test
    db = str(tmp_path / "funnel.db")
    monkeypatch.setattr(store_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(store_mod, "_STORE", None)
    monkeypatch.delenv("DEXIA_DB_PATH", raising=False)
    # deploy/activate don't need an ontology file: empty live registry
    monkeypatch.setattr(sim_api, "_live_registry", lambda: InMemoryRegistry())
    # avoid the file-queue side effect actually touching commands.json
    monkeypatch.setattr(sim_api, "_enqueue",
                        lambda action, **f: sim_api.CommandAck(ok=True, queued_id="cmd_test", action=action))
    return TestClient(sim_api.app)


def test_unauthenticated_401(client):
    r = client.post("/api/sim/activate")
    assert r.status_code == 401


def test_insufficient_clearance_403(client):
    # activate is commander-only; operator key -> 403
    r = client.post("/api/sim/activate", headers=OPERATOR)
    assert r.status_code == 403
    assert "clearance" in r.json()["detail"]


def test_accepted_202_with_lineage(client):
    r = client.post("/api/sim/deploy", headers=OPERATOR, json={"x": 5.0, "y": 6.0})
    assert r.status_code == 200  # FastAPI returns 200 for the GovernedAck body
    body = r.json()
    assert body["ok"] is True
    assert body["action"] == "deploy"
    assert body["lineage_id"] is not None
    assert body["command_id"] == "cmd_test"
    # commander can also activate
    r2 = client.post("/api/sim/activate", headers=COMMANDER)
    assert r2.status_code == 200 and r2.json()["action"] == "activate"


def test_ontology_guard_409(client, monkeypatch):
    # a LOST drone cannot be recalled -> ActionBus 409 with reason
    reg = InMemoryRegistry()
    reg.replace("DroneObject", [DroneObject(agent_id="agent_kami_3", status="lost")])
    monkeypatch.setattr(sim_api, "_live_registry", lambda: reg)
    r = client.post("/api/sim/recall", headers=COMMANDER, json={"agent_id": "agent_kami_3"})
    assert r.status_code == 409
    assert "LOST" in r.json()["detail"]["reason"]


def test_lineage_endpoint_records_principal(client):
    client.post("/api/sim/deploy", headers=COMMANDER, json={"x": 1.0, "y": 2.0})
    r = client.get("/ontology/lineage")
    assert r.status_code == 200
    rows = r.json()["lineage"]
    assert rows and rows[0]["principal"] == "commander"
    assert rows[0]["action"] == "deploy"
