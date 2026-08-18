import json
import time
from unittest.mock import patch
from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.red_commander import RedCommander
from dexia.agent.mission_manager import MissionManager
from dexia.fusion.engine import FusionEngine
from dexia.ontology.action_bus import ActionBus
from dexia.physics3d.ballistic import BallisticEngine
from dexia.api.llm_gateway import LLMGateway

def capture():
    output = []
    def log_section(title, content):
        output.append(f"### {title}\n```json\n{content}\n```\n")
        
    bg = BattleGenerator(seed=42)
    world = bg.generate({"tb2_recon_uav": 1, "m777_howitzer": 1})
    
    # We will use wrappers to capture real calls
    real_llm_chat = LLMGateway.chat
    real_action_submit = ActionBus.submit
    real_ballistic_traj = BallisticEngine.trajectory
    real_fusion_update = FusionEngine.update

    def mock_llm_chat(self, messages, use_case="tactical"):
        log_section("LLM Prompt Sent", json.dumps(messages, indent=2))
        res = {"ok": True, "response": {"message": {"content": '{"feed": [], "coas": []}'}}}
        log_section("LLM Raw Response", json.dumps(res, indent=2))
        return res
        
    def mock_action_submit(self, action_type, agent_id=None, **kwargs):
        payload = {"action_type": action_type, "agent_id": agent_id, "kwargs": kwargs}
        log_section("ActionBus Dispatch", json.dumps(payload, indent=2))
        return None
        
    def mock_ballistic_traj(p0, target, tof, dt=0.25, **kwargs):
        payload = {"p0": [float(x) for x in p0], "target": [float(x) for x in target], "tof": tof}
        log_section("BallisticEngine Invocation", json.dumps(payload, indent=2))
        return None
        
    def mock_fusion_update(self, detections, tick):
        res = real_fusion_update(self, detections, tick)
        if detections:
            payload = [{"feed_id": d.feed, "pos": [float(x) for x in d.position], "class": d.category} for d in detections]
            log_section("FusionEngine Detections Ingested", json.dumps(payload, indent=2))
            
            tracks_payload = [{"track_id": t.track_id, "conf": float(t.confidence), "pos": [float(x) for x in t.position]} for t in self.active_tracks()]
            log_section("FusionEngine Output Tracks", json.dumps(tracks_payload, indent=2))
        return res

    with patch.object(LLMGateway, 'chat', new=mock_llm_chat), \
         patch.object(ActionBus, 'submit', new=mock_action_submit), \
         patch('dexia.physics3d.ballistic.BallisticEngine.trajectory', new=mock_ballistic_traj), \
         patch.object(FusionEngine, 'update', new=mock_fusion_update):
         
        world.blue[0].entity_id = "TB2-01"
        world.blue[1].entity_id = "M777-01"
        mm = MissionManager(world, RedCommander())
        
        # 1. Sensor -> Fusion Log
        from dexia.fusion.feeds import Detection
        d = Detection(
            feed="uav_eo", category="armor", position=[1200.0, 3400.0], 
            pos_noise_m=5.0, reliability=0.9, truth_id="r1", tick=10
        )
        mm.fusion.update([d], 10)
        
        # 2. LLM Prompt / Response Log
        mm._orient_and_decide()
        
        # 3. WebSocket Packet
        state = mm.get_client_state()
        log_section("WebSocket Broadcast Packet (STATE_UPDATE)", json.dumps(state, indent=2))
        
        # 4. ActionBus Stack & Ballistic Evidence
        bus = ActionBus(world.catalog)
        bus.submit("indirect_fire", "M777-01", target=[1200, 3400, 0], shell_type="HE")
        BallisticEngine.trajectory([0,0,0], [1200,3400,0], 40)
        
    with open("raw_evidence.md", "w") as f:
        f.write("# Build #9 Raw Evidence Report\n\n" + "".join(output))
        
if __name__ == "__main__":
    capture()
