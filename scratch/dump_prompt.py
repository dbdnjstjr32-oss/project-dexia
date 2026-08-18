import json
import sys
from dexia.agent.battle_generator import BattleGenerator
from dexia.agent.mission_manager import MissionManager
from run_e2e_proof import StaticRedCommander, setup_engagement

def main():
    bg = BattleGenerator(seed=42)
    world = bg.generate({"tb2_recon_uav": 1, "m777_howitzer": 1}, difficulty="easy")
    tb2, arty, armor = setup_engagement(world)
    mm = MissionManager(world, StaticRedCommander())

    for tick in range(1, 13):
        mm.run_cycle()
        tracks = mm.fusion.snapshot()
        print(f"Tick {tick}: tracks={[ (t['track_id'], t['category'], t['confidence']) for t in tracks ]}")

if __name__ == "__main__":
    main()
