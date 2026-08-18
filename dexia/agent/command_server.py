"""Command Server.

FastAPI server exposing a WebSocket endpoint for the Next.js Operations UI.
Runs the MissionManager loop in an asyncio background task.
"""

import asyncio
import json
import os
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.red_commander import RedCommander
from dexia.agent.mission_manager import MissionManager

# Only these browser origins may drive the (single, shared) mission session.
# CORS does NOT protect WebSockets, so the same allow-list is re-checked on the
# WS handshake below. Override for a non-default HUD origin via DEXIA_HUD_ORIGIN.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "DEXIA_HUD_ORIGIN", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",") if o.strip()
]
# Optional shared secret for non-browser clients (no Origin header): pass as
# ws://host:8001/ws/command?token=<DEXIA_WS_TOKEN>.
WS_TOKEN = os.environ.get("DEXIA_WS_TOKEN") or None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # Drop sockets that fail to receive instead of silently retrying a dead
        # connection every tick (which leaked closed sockets + repeated errors).
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for c in dead:
            self.disconnect(c)

manager = ConnectionManager()

# Global state for the session
session = {
    "manager": None,
    "running": False,
    "task": None
}


async def _cancel_task(task) -> None:
    """Cancel a background task and await it so it isn't left dangling
    ('Task was destroyed but it is pending') or leaking its child loops."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _stepping_loop():
    """Advance the OODA loop ~1 Hz. run_cycle is offloaded to a worker thread so its
    blocking LLM call never stalls the event loop (Phase F): the broadcaster keeps
    streaming while the AIP reasons."""
    while session["running"] and session["manager"]:
        mm: MissionManager = session["manager"]
        if not mm.paused:
            await asyncio.to_thread(mm.run_cycle)
        await asyncio.sleep(1.0 / mm.speed)


async def _broadcast_loop():
    """Stream client state at a steady 2 Hz, independent of reasoning latency.
    get_client_state takes the state lock only briefly (never across the LLM)."""
    while session["running"] and session["manager"]:
        mm: MissionManager = session["manager"]
        state = mm.get_client_state()
        await manager.broadcast(json.dumps({"type": "STATE_UPDATE", "data": state}))
        await asyncio.sleep(0.5 / mm.speed)


async def simulation_loop():
    """Background task: step + broadcast run concurrently (decoupled)."""
    await asyncio.gather(_stepping_loop(), _broadcast_loop())

@app.websocket("/ws/command")
async def websocket_endpoint(websocket: WebSocket):
    # Reject cross-site / unauthenticated handshakes BEFORE accepting: a browser
    # sends Origin (must be allow-listed); a non-browser client may instead
    # present the shared DEXIA_WS_TOKEN. This endpoint mutates a live mission
    # (START/RESET/APPROVE/...), so an open WS = anyone can drive the sim.
    origin = websocket.headers.get("origin")
    token = websocket.query_params.get("token")
    origin_ok = origin in ALLOWED_ORIGINS
    token_ok = bool(WS_TOKEN) and token == WS_TOKEN
    if not (origin_ok or token_ok):
        await websocket.close(code=1008)  # policy violation
        return
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            cmd_type = payload.get("type")
            
            if cmd_type == "LIST_BATTLEFIELDS":
                from dexia.scenario.battlefields import get_catalog as bf_catalog
                cat = [{"id": b["id"], "region": b["region"], "name": b["name"],
                        "location": b["location"], "size_m": b["size_m"],
                        "climate": b["climate"], "difficulty": b["difficulty"]}
                       for b in bf_catalog()]
                await websocket.send_text(json.dumps({"type": "BATTLEFIELDS", "data": cat}))

            elif cmd_type == "START":
                generator = BattleGenerator()
                bf_id = payload.get("battlefield_id")
                if bf_id:
                    # Activate a pre-designated battlefield (region terrain + size);
                    # equipment is randomly populated onto it, then enemy area fogged.
                    from dexia.scenario.battlefields import get_battlefield
                    bf = get_battlefield(bf_id)
                    if bf is None:
                        await websocket.send_text(json.dumps(
                            {"type": "ERROR", "message": f"unknown battlefield '{bf_id}'"}))
                        continue
                    world = generator.from_battlefield(bf)
                    info = f"Activated {bf['name']} ({bf['region']}) — loading forces…"
                else:
                    # ad-hoc generation (user-specified forces/difficulty)
                    bf = payload.get("blue_forces", {"tb2_recon_uav": 1, "m777_howitzer": 2})
                    difficulty = payload.get("difficulty", "medium")
                    world = generator.generate(blue_forces=bf, difficulty=difficulty)
                    info = "Simulation started."
                red_cmd = RedCommander()
                
                mm = MissionManager(world, red_cmd)
                session["manager"] = mm
                session["running"] = True
                
                await _cancel_task(session["task"])
                session["task"] = asyncio.create_task(simulation_loop())
                
                await websocket.send_text(json.dumps({"type": "INFO", "message": info}))
                
            elif cmd_type == "APPROVE" and session["manager"]:
                coa_id = payload.get("coa_id")
                # approve may execute the COA (LLM/physics) — offload off the loop
                await asyncio.to_thread(session["manager"].approve_coa, coa_id)

            elif cmd_type == "REJECT" and session["manager"]:
                session["manager"].reject_coa(payload.get("coa_id"))

            elif cmd_type == "RECON" and session["manager"]:
                # 사용자가 AIP에게 정찰 지시 → AIP가 지형 기반 루트 계획 후 비행
                await asyncio.to_thread(session["manager"].command_recon, payload.get("area"))

            elif cmd_type == "STRIKE_OPTIONS" and session["manager"]:
                # 최적 타격전술 요청 → 안1/안2/안3 산출 (LLM; offload off the loop)
                await asyncio.to_thread(session["manager"].generate_coa_options)

            elif cmd_type == "BDA" and session["manager"]:
                # 피해평가 (LLM; offload off the loop)
                await asyncio.to_thread(session["manager"].assess_bda)

            elif cmd_type == "MODIFY" and session["manager"]:
                text = payload.get("text")
                await asyncio.to_thread(session["manager"].modify_plan, text)
                
            elif cmd_type == "PAUSE" and session["manager"]:
                session["manager"].paused = True
                
            elif cmd_type == "RESUME" and session["manager"]:
                session["manager"].paused = False
                
            elif cmd_type == "RESET":
                session["running"] = False
                await _cancel_task(session["task"])
                session["task"] = None
                session["manager"] = None
                await websocket.send_text(json.dumps({"type": "RESET_ACK"}))

            elif cmd_type == "SET_SPEED" and session["manager"]:
                session["manager"].speed = float(payload.get("speed", 1.0))
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # Bind localhost by default (single-node demo). Override with DEXIA_BIND_HOST
    # only behind a trusted reverse proxy / when you've set DEXIA_WS_TOKEN.
    host = os.environ.get("DEXIA_BIND_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=8001)
