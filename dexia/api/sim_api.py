"""Dexia Simulator Control API (FastAPI) — Phase B of the AIP stack.

A microservice that wraps the simulator's command plane. The LLM tactical agent
(and the HITL frontend) call these typed, Pydantic-validated endpoints instead
of writing files by hand. Each call is enqueued onto the existing command queue
(``commands.json``), which the running ``telemetry_stream.py`` drains and applies
to ``DroneMARLEnv`` (Object-Pool activation, enemy/friendly placement, ARM gate).

Design: this is a thin, stateless control plane over the event queue — so it
needs neither Ray nor MuJoCo, and in Phase A the queue simply swaps to Redis
Streams with no endpoint changes.

Run:
    .venv312\\Scripts\\python.exe -m uvicorn dexia.api.sim_api:app --port 8000
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..integrations.command_queue import append_command, DEFAULT_PATH as COMMANDS_PATH
from ..ai.tactical_agent import TacticalAgent

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TELEMETRY_PATH = os.path.join(ROOT, "telemetry.json")
ONTOLOGY_PATH = os.path.join(ROOT, "ontology_state.json")

# Lazily-constructed AI Staff agent (loads the Ollama client once).
_AGENT: TacticalAgent | None = None


def _agent() -> TacticalAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = TacticalAgent()
    return _AGENT

app = FastAPI(
    title="Dexia Simulator Control API",
    version="0.1.0",
    description="AIP control plane over the MuJoCo MARL simulator command queue.",
)


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #
class DeployRequest(BaseModel):
    x: float = Field(..., description="local sim X [m]")
    y: float = Field(..., description="local sim Y [m]")
    z: float = Field(0.3, description="spawn altitude [m] (staged on ground)")
    profile: Optional[dict] = Field(None, description="airframe profile dict (Garage)")


class PlaceRequest(BaseModel):
    x: float
    y: float


class RemoveRequest(BaseModel):
    agent_id: str


class CommandAck(BaseModel):
    ok: bool
    queued_id: str
    action: str
    detail: Optional[str] = None


# --------------------------------------------------------------------------- #
def _enqueue(action: str, **fields: Any) -> CommandAck:
    cmd = {"id": "cmd_" + uuid.uuid4().hex[:14], "action": action, **fields}
    try:
        append_command(cmd, path=COMMANDS_PATH)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"enqueue failed: {exc}")
    return CommandAck(ok=True, queued_id=cmd["id"], action=action)


# --------------------------------------------------------------------------- #
# Read endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/sim/health")
def health() -> dict:
    return {"ok": True, "service": "dexia-sim-control", "commands_path": COMMANDS_PATH}


@app.get("/api/sim/telemetry")
def telemetry() -> dict:
    """Latest world snapshot written by the streamer."""
    if not os.path.exists(TELEMETRY_PATH):
        raise HTTPException(status_code=503, detail="no telemetry yet — is the streamer running?")
    try:
        with open(TELEMETRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"telemetry read failed: {exc}")


# --------------------------------------------------------------------------- #
# Control endpoints (LLM/HITL tools)
# --------------------------------------------------------------------------- #
@app.post("/api/sim/deploy", response_model=CommandAck)
def deploy_drone(req: DeployRequest) -> CommandAck:
    """Deploy a friendly drone from the Object Pool (staged until ACTIVATE)."""
    return _enqueue("spawn", x=req.x, y=req.y, z=req.z, profile=req.profile)


@app.post("/api/sim/enemy", response_model=CommandAck)
def set_enemy(req: PlaceRequest) -> CommandAck:
    """Place the enemy strongpoint (AA battery + target)."""
    return _enqueue("set_enemy", x=req.x, y=req.y)


@app.post("/api/sim/friendly", response_model=CommandAck)
def set_friendly(req: PlaceRequest) -> CommandAck:
    """Place the friendly camp (base station + loiter centre)."""
    return _enqueue("set_friendly", x=req.x, y=req.y)


@app.post("/api/sim/activate", response_model=CommandAck)
def activate() -> CommandAck:
    """ARM the scenario — staged drones take off under physics; AA goes live."""
    return _enqueue("arm")


@app.post("/api/sim/standby", response_model=CommandAck)
def standby() -> CommandAck:
    """DISARM — freeze drones at their placed positions, AA holds fire."""
    return _enqueue("disarm")


@app.post("/api/sim/recall", response_model=CommandAck)
def recall_drone(req: RemoveRequest) -> CommandAck:
    """Return-to-base / recall a drone to the pool (or flag destroyed)."""
    return _enqueue("remove", agent_id=req.agent_id)


@app.post("/api/sim/clear", response_model=CommandAck)
def clear_scenario() -> CommandAck:
    """Recall all deployed drones and disarm."""
    return _enqueue("clear")


# Tool catalogue the LLM agent introspects (kept in sync with the endpoints).
TOOL_CATALOG = [
    {"action": "deploy", "endpoint": "/api/sim/deploy", "args": ["x", "y", "z?", "profile?"],
     "desc": "Deploy a friendly strike/recon drone at local (x,y)."},
    {"action": "set_enemy", "endpoint": "/api/sim/enemy", "args": ["x", "y"],
     "desc": "Mark/relocate the enemy AA strongpoint."},
    {"action": "set_friendly", "endpoint": "/api/sim/friendly", "args": ["x", "y"],
     "desc": "Mark/relocate the friendly base."},
    {"action": "activate", "endpoint": "/api/sim/activate", "args": [],
     "desc": "ARM — launch staged drones (physics on)."},
    {"action": "standby", "endpoint": "/api/sim/standby", "args": [],
     "desc": "DISARM — hold."},
    {"action": "recall", "endpoint": "/api/sim/recall", "args": ["agent_id"],
     "desc": "Recall/destroy a drone."},
]


@app.get("/api/sim/tools")
def tools() -> dict:
    return {"tools": TOOL_CATALOG}


# --------------------------------------------------------------------------- #
# OSS — Ontology query layer (Object Set Service, Phase 7)
# Reads the snapshot the streamer writes (ontology_state.json).
# --------------------------------------------------------------------------- #
def _load_ontology() -> dict:
    if not os.path.exists(ONTOLOGY_PATH):
        raise HTTPException(status_code=503, detail="no ontology — is the streamer running?")
    try:
        with open(ONTOLOGY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ontology read failed: {exc}")


@app.get("/ontology/snapshot")
def ontology_snapshot() -> dict:
    return _load_ontology()


@app.get("/ontology/drones")
def ontology_drones(role: Optional[str] = None, status: Optional[str] = None) -> dict:
    drones = _load_ontology()["objects"].get("DroneObject", [])
    if role:
        drones = [d for d in drones if d.get("role") == role or d.get("kind") == role]
    if status:
        drones = [d for d in drones if d.get("status") == status]
    return {"count": len(drones), "drones": drones}


@app.get("/ontology/drones/{drone_id}")
def ontology_drone(drone_id: str) -> dict:
    ont = _load_ontology()
    d = next((o for o in ont["objects"].get("DroneObject", []) if o.get("agent_id") == drone_id), None)
    if d is None:
        raise HTTPException(status_code=404, detail=f"drone '{drone_id}' not found")
    links = [
        l for l in ont.get("links", [])
        if l.get("drone_id") == drone_id or l.get("source") == drone_id or l.get("target") == drone_id
    ]
    return {"drone": d, "links": links}


@app.get("/ontology/threats")
def ontology_threats() -> dict:
    threats = _load_ontology()["objects"].get("ThreatObject", [])
    return {"count": len(threats), "threats": threats}


@app.get("/ontology/killchain")
def ontology_killchain() -> dict:
    links = [l for l in _load_ontology().get("links", []) if l.get("link_type") == "KillChainLink"]
    return {"count": len(links), "links": links}


@app.get("/ontology/mission")
def ontology_mission() -> dict:
    missions = _load_ontology()["objects"].get("MissionObject", [])
    return missions[0] if missions else {}


# --------------------------------------------------------------------------- #
# AI Staff — assessment (HITL Sprint 1)
# --------------------------------------------------------------------------- #
class AssessResponse(BaseModel):
    ok: bool
    model: Optional[str] = None
    assessment: str = ""
    recommendations: list[dict] = []
    error: Optional[str] = None


@app.post("/api/sim/assess", response_model=AssessResponse)
def assess() -> AssessResponse:
    """Run the local LLM tactical agent on the CURRENT battlefield telemetry and
    return a Course of Action (assessment + recommended tool calls). Does NOT
    execute — that is gated by human approval in the HUD (Phase D / HITL)."""
    if not os.path.exists(TELEMETRY_PATH):
        raise HTTPException(status_code=503, detail="no telemetry — is the streamer running?")
    try:
        with open(TELEMETRY_PATH, "r", encoding="utf-8") as f:
            tele = json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"telemetry read failed: {exc}")

    coa = _agent().assess(tele)
    # annotate each recommendation with the concrete control-API call to run on approval
    for r in coa.get("recommendations", []):
        r["api"] = TacticalAgent.to_api_call(r)
    return AssessResponse(
        ok=bool(coa.get("ok")),
        model=coa.get("model"),
        assessment=coa.get("assessment", ""),
        recommendations=coa.get("recommendations", []),
        error=coa.get("error"),
    )
