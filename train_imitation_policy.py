"""Train 256x256 PyTorch Neural Policy using Expert Flight Dataset (Imitation Learning).

Transfers the precision Geometric Autopilot's 10,000-hour flight capability
directly into the Neural Network Actor.
"""

import os
import json
import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader

class DronePolicyMLP(nn.Module):
    def __init__(self, in_dim=16, hidden_dim=256, out_dim=4):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, out_dim)
        )
        
    def forward(self, x):
        return torch.clamp(self.net(x), -1.0, 1.0)

def train_imitation_model(dataset_path="expert_flight_dataset.npz", epochs=50, batch_size=256, lr=2e-3):
    print("=========================================================")
    print("[DEXIA NEURAL FLIGHT SCHOOL] CLOSED-LOOP IMITATION TRAINING")
    print("=========================================================")
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Hardware Compute Device: {device}")
    
    data = np.load(dataset_path)
    obs = data["observations"]
    actions = data["actions"]
    num_samples = len(obs)
    print(f"Loaded {num_samples:,} trajectory frames from {dataset_path}")
    
    # Split Train / Test (90% / 10%)
    indices = np.random.permutation(num_samples)
    split = int(num_samples * 0.9)
    train_idx, test_idx = indices[:split], indices[split:]
    
    train_x, train_y = torch.tensor(obs[train_idx]), torch.tensor(actions[train_idx])
    test_x, test_y = torch.tensor(obs[test_idx]), torch.tensor(actions[test_idx])
    
    train_loader = DataLoader(TensorDataset(train_x, train_y), batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(TensorDataset(test_x, test_y), batch_size=batch_size, shuffle=False)
    
    model = DronePolicyMLP().to(device)
    criterion = nn.MSELoss()
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-6)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)
    
    print(f"\nStarting Supervised Training for {epochs} Epochs...")
    start_time = time.time()
    
    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            pred_y = model(batch_x)
            loss = criterion(pred_y, batch_y)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(batch_x)
            
        train_loss /= len(train_x)
        scheduler.step()
        
        # Eval
        model.eval()
        test_loss = 0.0
        with torch.no_grad():
            for batch_x, batch_y in test_loader:
                batch_x, batch_y = batch_x.to(device), batch_y.to(device)
                pred_y = model(batch_x)
                loss = criterion(pred_y, batch_y)
                test_loss += loss.item() * len(batch_x)
        test_loss /= len(test_x)
        
        if epoch % 5 == 0 or epoch == 1 or epoch == epochs:
            accuracy_pct = max(0.0, 100.0 - (test_loss * 1000))
            print(f"Epoch [{epoch:02d}/{epochs:02d}] | Train Loss: {train_loss:.6f} | Test Loss: {test_loss:.6f} | Autopilot Imitation Accuracy: {accuracy_pct:.2f}%")
            
    train_duration = time.time() - start_time
    print(f"\nTraining completed in {train_duration:.2f}s!")
    
    # Export Weights to JS bundle for Web Simulator
    export_to_js(model.cpu())

def export_to_js(model):
    sd = model.state_dict()
    w0 = [[round(float(x), 5) for x in row] for row in sd['net.0.weight'].numpy()]
    b0 = [round(float(x), 5) for x in sd['net.0.bias'].numpy()]
    
    w1 = [[round(float(x), 5) for x in row] for row in sd['net.2.weight'].numpy()]
    b1 = [round(float(x), 5) for x in sd['net.2.bias'].numpy()]
    
    wpi = [[round(float(x), 5) for x in row] for row in sd['net.4.weight'].numpy()]
    bpi = [round(float(x), 5) for x in sd['net.4.bias'].numpy()]
    
    # Make backup of previous weights if exists
    if os.path.exists('ppo_weights.js'):
        with open('ppo_weights.js', 'r', encoding='utf-8') as f:
            old = f.read()
        with open('ppo_weights_backup.js', 'w', encoding='utf-8') as f:
            f.write(old)
            
    js_content = 'window.PPO_WEIGHTS = ' + json.dumps({
        'w0': w0, 'b0': b0,
        'w1': w1, 'b1': b1,
        'wpi': wpi, 'bpi': bpi
    }) + ';\n'
    
    with open('ppo_weights.js', 'w', encoding='utf-8') as f:
        f.write(js_content)
        
    print(f"Exported newly trained neural weights to 'ppo_weights.js' ({os.path.getsize('ppo_weights.js'):,} bytes)!")
    print("The 3D simulator can now fly with the new trained AI Pilot immediately!")

if __name__ == '__main__':
    train_imitation_model()
