"""Command Server.

FastAPI server exposing a WebSocket endpoint for the Next.js Operations UI.
Runs the MissionManager loop in an asyncio background task.
"""

import asyncio
import json
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.red_commander import RedCommander
from dexia.agent.mission_manager import MissionManager

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# Global state for the session
session = {
    "manager": None,
    "running": False,
    "task": None
}

async def _stepping_loop():
    """Advance the OODA loop ~1 Hz. run_cycle is offloaded to a worker thread so its
    blocking LLM call never stalls the event loop (Phase F): the broadcaster keeps
    streaming while the AIP reasons."""
    while session["running"] and session["manager"]:
        mm: MissionManager = session["manager"]
        if not mm.paused:
            await asyncio.to_thread(mm.run_cycle)
        await asyncio.sleep(1.0)


async def _broadcast_loop():
    """Stream client state at a steady 2 Hz, independent of reasoning latency.
    get_client_state takes the state lock only briefly (never across the LLM)."""
    while session["running"] and session["manager"]:
        mm: MissionManager = session["manager"]
        state = mm.get_client_state()
        await manager.broadcast(json.dumps({"type": "STATE_UPDATE", "data": state}))
        await asyncio.sleep(0.5)


async def simulation_loop():
    """Background task: step + broadcast run concurrently (decoupled)."""
    await asyncio.gather(_stepping_loop(), _broadcast_loop())

@app.websocket("/ws/command")
async def websocket_endpoint(websocket: WebSocket):
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
                
                if session["task"]:
                    session["task"].cancel()
                session["task"] = asyncio.create_task(simulation_loop())
                
                await websocket.send_text(json.dumps({"type": "INFO", "message": info}))
                
            elif cmd_type == "APPROVE" and session["manager"]:
                coa_id = payload.get("coa_id")
                session["manager"].approve_coa(coa_id)

            elif cmd_type == "REJECT" and session["manager"]:
                session["manager"].reject_coa(payload.get("coa_id"))

            elif cmd_type == "RECON" and session["manager"]:
                # 사용자가 AIP에게 정찰 지시 → AIP가 지형 기반 루트 계획 후 비행
                await asyncio.to_thread(session["manager"].command_recon, payload.get("area"))

            elif cmd_type == "STRIKE_OPTIONS" and session["manager"]:
                # 최적 타격전술 요청 → 안1/안2/안3 산출
                session["manager"].generate_coa_options()

            elif cmd_type == "BDA" and session["manager"]:
                session["manager"].assess_bda()

            elif cmd_type == "MODIFY" and session["manager"]:
                text = payload.get("text")
                await asyncio.to_thread(session["manager"].modify_plan, text)
                
            elif cmd_type == "PAUSE" and session["manager"]:
                session["manager"].paused = True
                
            elif cmd_type == "RESUME" and session["manager"]:
                session["manager"].paused = False
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
