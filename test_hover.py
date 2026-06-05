import os
import sys
import numpy as np
import torch

# Add local path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ray.rllib.core.rl_module.rl_module import RLModule
from ray.rllib.core.columns import Columns
from dexia.envs.drone_env_6dof import DroneFlightSchoolEnv, STAGE_HOVER

CHECKPOINT_PATH = os.path.abspath("checkpoints/phase2_ppo/learner_group/learner/rl_module/default_policy")

def deterministic_action(module, obs: np.ndarray) -> np.ndarray:
    batch = {Columns.OBS: torch.from_numpy(obs[None]).float()}
    with torch.no_grad():
        out = module.forward_inference(batch)
    dist_inputs = out[Columns.ACTION_DIST_INPUTS][0].cpu().numpy()
    mean = dist_inputs[:4]
    return np.clip(mean, -1.0, 1.0).astype(np.float64)

def main():
    print(f"Loading policy from {CHECKPOINT_PATH}...")
    if not os.path.exists(CHECKPOINT_PATH):
        print("Checkpoint not found!")
        return 1
        
    module = RLModule.from_checkpoint(CHECKPOINT_PATH)
    
    env = DroneFlightSchoolEnv({
        "curriculum_stage": STAGE_HOVER,
        "max_steps": 500,
        "spawn_height": 1.5,
        "goal_radius": 0.4
    })
    
    obs, info = env.reset(seed=42)
    done = False
    step = 0
    total_dist = 0.0
    crashed = False
    
    print("\n--- Running Hover Episode ---")
    while not done:
        action = deterministic_action(module, obs)
        obs, reward, terminated, truncated, info = env.step(action)
        dist = info["distance_to_target"]
        total_dist += dist
        step += 1
        
        pos = env.engine.get_state().position
        roll, pitch, yaw = env.engine.get_state().orientation
        print(f"Step {step:3d} | Pos: {np.round(pos, 3)} | Dist: {dist:.3f} m | Tilt: {info['tilt_deg']:.1f} deg | Roll/Pitch/Yaw: {np.round([np.rad2deg(roll), np.rad2deg(pitch), np.rad2deg(yaw)], 1)} | Action: {np.round(action, 2)}")
            
        if terminated:
            crashed = info.get("crashed", False)
            done = True
        if truncated:
            done = True
            
    avg_dist = total_dist / step
    print("\n--- Summary ---")
    print(f"Steps run: {step}")
    print(f"Crashed: {crashed}")
    print(f"Average Distance to Target: {avg_dist:.3f} m ({avg_dist * 100:.1f} cm)")
    print(f"Hover within 40cm requirement met: {avg_dist <= 0.4}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
