import asyncio
import json
import os
import websockets
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

async def test_workflow():
    # The command server binds port 8001 (8000 is the FastAPI evals/sim API,
    # which has no /ws/command). As a non-browser client we carry the shared
    # DEXIA_WS_TOKEN so the server's WS Origin/token check lets us in.
    token = os.environ.get("DEXIA_WS_TOKEN", "")
    uri = "ws://localhost:8001/ws/command" + (f"?token={token}" if token else "")
    print("[1] Connecting to command server...")
    
    async with websockets.connect(uri) as websocket:
        # Reset any existing session
        await websocket.send(json.dumps({"type": "RESET"}))
        await websocket.recv() # wait for RESET_ACK
        
        # 1. Start simulation
        print("[2] Starting simulation...")
        await websocket.send(json.dumps({"type": "START"}))
        
        # 2. Set speed to 20x for faster testing
        await websocket.send(json.dumps({"type": "SET_SPEED", "speed": 20.0}))
        
        # 3. Request Recon
        print("[3] Commander requests RECON...")
        await websocket.send(json.dumps({"type": "RECON"}))
        
        # Track state
        found_target = False
        approved_strike = False
        strike_landed = False
        neutralized_count = 0
        seen_feed = set()
        
        start_time = time.time()
        timeout = 180 # 3 minutes real-time timeout
        
        while True:
            if time.time() - start_time > timeout:
                print("TIMEOUT REACHED!")
                break
                
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                data = json.loads(msg)
                
                if data["type"] == "STATE_UPDATE":
                    state = data["data"]
                    
                    # Print feed events
                    feed = state.get("feed", [])
                    for f in feed:
                        msg_text = f.get("message", "")
                        if msg_text not in seen_feed:
                            print(f"[FEED - {f.get('type')}] {msg_text}")
                            seen_feed.add(msg_text)
                    
                    # Watch the feed for events
                    feed = state.get("feed", [])
                    for f in feed[-3:]:
                        if "type" in f and f["type"] == "BDA" and "타격 결과" in f["message"]:
                            print(f"[BDA] {f['message']}")
                            strike_landed = True
                            if "0 neutralized" not in f["message"] and "타겟 없음" not in f["message"]:
                                neutralized_count += 1
                                
                    # Step 4: Check if we have tracks with high enough confidence
                    if not found_target:
                        tracks = state.get("tracks", [])
                        valid_tracks = [t for t in tracks if t.get("confidence", 0) > 0.5]
                        if valid_tracks:
                            print(f"[4] High-confidence track found! Requesting STRIKE_OPTIONS...")
                            await websocket.send(json.dumps({"type": "STRIKE_OPTIONS"}))
                            found_target = True
                            
                    # Step 5: Check approval queue and approve
                    if found_target and not approved_strike:
                        queue = state.get("approval_queue", [])
                        if queue:
                            best_coa = queue[0]
                            print(f"[5] AI proposed {len(queue)} options. Approving {best_coa['id']} (action: {best_coa['action']}, target: {best_coa['target']})")
                            await websocket.send(json.dumps({"type": "APPROVE", "coa_id": best_coa["id"]}))
                            approved_strike = True
                            
                    # Step 6: End test after strike lands
                    if strike_landed:
                        print("\n=== TEST COMPLETED ===")
                        if neutralized_count > 0:
                            print("RESULT: SUCCESS - Target was successfully neutralized using the new physics calculations.")
                        else:
                            print("RESULT: FAILURE - Target was missed (0 neutralized).")
                        break
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    asyncio.run(test_workflow())
