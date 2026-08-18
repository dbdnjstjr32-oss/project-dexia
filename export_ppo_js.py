import os
import json
import torch
from ray.rllib.core.rl_module.rl_module import RLModule

ckpt_path = os.path.abspath('checkpoints/phase2_ppo/learner_group/learner/rl_module/default_policy')
module = RLModule.from_checkpoint(ckpt_path)
sd = module.state_dict()

# Extract Actor weights (round to 5 decimals for fast loading)
w0 = [[round(float(x), 5) for x in row] for row in sd['encoder.actor_encoder.net.mlp.0.weight'].cpu().numpy()]
b0 = [round(float(x), 5) for x in sd['encoder.actor_encoder.net.mlp.0.bias'].cpu().numpy()]

w1 = [[round(float(x), 5) for x in row] for row in sd['encoder.actor_encoder.net.mlp.2.weight'].cpu().numpy()]
b1 = [round(float(x), 5) for x in sd['encoder.actor_encoder.net.mlp.2.bias'].cpu().numpy()]

wpi = [[round(float(x), 5) for x in row] for row in sd['pi.net.mlp.0.weight'].cpu().numpy()]
bpi = [round(float(x), 5) for x in sd['pi.net.mlp.0.bias'].cpu().numpy()]

js_content = 'window.PPO_WEIGHTS = ' + json.dumps({
    'w0': w0, 'b0': b0,
    'w1': w1, 'b1': b1,
    'wpi': wpi, 'bpi': bpi
}) + ';\n'

with open('ppo_weights.js', 'w', encoding='utf-8') as f:
    f.write(js_content)

print(f"Successfully generated ppo_weights.js ({os.path.getsize('ppo_weights.js')} bytes)")
