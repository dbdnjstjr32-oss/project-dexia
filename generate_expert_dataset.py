"""Closed-Loop Trajectory Dataset Generator for 6-DOF Drone Imitation Learning.

Simulates 200 full flight trajectories (100,000 sequential physics steps)
under realistic 6-DOF Newton-Euler dynamics, capturing the exact closed-loop
state distribution of the Geometric Autopilot.
"""

import numpy as np
import time
import os

MASS = 0.6
GRAVITY = 9.81
ARM_LENGTH = 0.11
MAX_THRUST = 7.0
KAPPA = 0.0201
DRAG_COEFF = 0.22
ROT_DAMP_COEFF = 0.12
DT = 0.01666 # 60Hz

def wrap_angle(rad):
    return (rad + np.pi) % (2 * np.pi) - np.pi

def compute_expert_thrusts(pos, vel, euler, omega, target_pos, target_yaw):
    pos_err = target_pos - pos
    
    # 1. Outer Loop
    a_cmd_x = 4.5 * pos_err[0] - 3.4 * vel[0]
    a_cmd_y = 4.5 * pos_err[1] - 3.4 * vel[1]
    a_cmd_z = 6.8 * pos_err[2] - 4.8 * vel[2] + GRAVITY
    
    cos_tilt = max(np.cos(euler[0]) * np.cos(euler[1]), 0.7)
    thrust_total = np.clip((MASS * a_cmd_z) / cos_tilt, 0.5, 4 * MAX_THRUST * 0.75)
    
    cy = np.cos(euler[2])
    sy = np.sin(euler[2])
    a_fwd = -a_cmd_x * sy + a_cmd_y * cy
    a_right = a_cmd_x * cy + a_cmd_y * sy
    
    roll_des = np.clip(-a_fwd / GRAVITY, -0.40, 0.40)
    pitch_des = np.clip(a_right / GRAVITY, -0.40, 0.40)
    yaw_des = target_yaw
    
    # 2. Inner Loop
    e_roll = roll_des - euler[0]
    e_pitch = pitch_des - euler[1]
    e_yaw = wrap_angle(yaw_des - euler[2])
    
    tau_x = 14.0 * e_roll - 2.2 * omega[0]
    tau_y = 14.0 * e_pitch - 2.2 * omega[1]
    tau_z = 6.0 * e_yaw - 1.2 * omega[2]
    
    t_base = thrust_total / 4.0
    max_d_att = 0.55 * t_base
    dt_x = np.clip(tau_x / (4.0 * ARM_LENGTH), -max_d_att, max_d_att)
    dt_y = np.clip(tau_y / (4.0 * ARM_LENGTH), -max_d_att, max_d_att)
    dt_z = np.clip(tau_z / (4.0 * KAPPA), -0.25 * t_base, 0.25 * t_base)
    
    T0 = np.clip(t_base + dt_x - dt_y + dt_z, 0.0, MAX_THRUST)
    T1 = np.clip(t_base + dt_x + dt_y - dt_z, 0.0, MAX_THRUST)
    T2 = np.clip(t_base - dt_x + dt_y + dt_z, 0.0, MAX_THRUST)
    T3 = np.clip(t_base - dt_x - dt_y - dt_z, 0.0, MAX_THRUST)
    
    return np.array([T0, T1, T2, T3], dtype=np.float32)

def simulate_trajectory_dataset(num_episodes=400, episode_length=300, save_path="expert_flight_dataset.npz"):
    print(f"Simulating {num_episodes} closed-loop trajectories ({num_episodes * episode_length:,} total steps)...")
    start_t = time.time()
    np.random.seed(42)
    
    total_steps = num_episodes * episode_length
    obs_list = np.zeros((total_steps, 16), dtype=np.float32)
    act_list = np.zeros((total_steps, 4), dtype=np.float32)
    
    idx = 0
    for ep in range(num_episodes):
        # Random initial position & initial target
        pos = np.array([np.random.uniform(-2.5, 2.5), np.random.uniform(-2.5, 2.5), np.random.uniform(1.2, 2.5)])
        vel = np.random.uniform(-0.5, 0.5, 3)
        euler = np.random.uniform(-0.1, 0.1, 3)
        omega = np.random.uniform(-0.2, 0.2, 3)
        
        target = np.array([np.random.uniform(-3.0, 3.0), np.random.uniform(-3.0, 3.0), np.random.uniform(1.2, 3.2)])
        target_yaw = np.random.uniform(-np.pi, np.pi)
        prev_action = np.zeros(4, dtype=np.float32)
        
        for step in range(episode_length):
            # Switch target every 120 steps (simulate waypoint mission)
            if step > 0 and step % 120 == 0:
                target = np.array([np.random.uniform(-3.0, 3.0), np.random.uniform(-3.0, 3.0), np.random.uniform(1.2, 3.2)])
                target_yaw = np.random.uniform(-np.pi, np.pi)
                
            pos_err = target - pos
            obs = np.array([
                np.clip(pos_err[0], -3.0, 3.0),
                np.clip(pos_err[1], -3.0, 3.0),
                np.clip(pos_err[2], -2.0, 2.0),
                np.clip(vel[0], -4.0, 4.0),
                np.clip(vel[1], -4.0, 4.0),
                np.clip(vel[2], -4.0, 4.0),
                wrap_angle(euler[0]),
                wrap_angle(euler[1]),
                wrap_angle(euler[2] - target_yaw),
                np.clip(omega[0], -6.0, 6.0),
                np.clip(omega[1], -6.0, 6.0),
                np.clip(omega[2], -6.0, 6.0),
                prev_action[0], prev_action[1], prev_action[2], prev_action[3]
            ], dtype=np.float32)
            
            thrusts = compute_expert_thrusts(pos, vel, euler, omega, target, target_yaw)
            actions = (thrusts / (0.5 * MAX_THRUST)) - 1.0
            
            obs_list[idx] = obs
            act_list[idx] = actions
            idx += 1
            prev_action = actions.copy()
            
            # Physics Step (Newton-Euler)
            total_thrust = np.sum(thrusts)
            tau_x = ARM_LENGTH * (thrusts[0] + thrusts[1] - thrusts[2] - thrusts[3])
            tau_y = ARM_LENGTH * (-thrusts[0] + thrusts[1] + thrusts[2] - thrusts[3])
            tau_z = KAPPA * (thrusts[0] - thrusts[1] + thrusts[2] - thrusts[3])
            
            Ixx, Iyy, Izz = 0.003, 0.003, 0.005
            alpha_x = (tau_x - (Izz - Iyy) * omega[1] * omega[2] - ROT_DAMP_COEFF * omega[0]) / Ixx
            alpha_y = (tau_y - (Ixx - Izz) * omega[2] * omega[0] - ROT_DAMP_COEFF * omega[1]) / Iyy
            alpha_z = (tau_z - (Iyy - Ixx) * omega[0] * omega[1] - ROT_DAMP_COEFF * omega[2]) / Izz
            
            omega[0] += alpha_x * DT
            omega[1] += alpha_y * DT
            omega[2] += alpha_z * DT
            
            euler[0] = wrap_angle(euler[0] + omega[0] * DT)
            euler[1] = wrap_angle(euler[1] + omega[1] * DT)
            euler[2] = wrap_angle(euler[2] + omega[2] * DT)
            
            # Rotation matrix ZYX
            cr, sr = np.cos(euler[0]), np.sin(euler[0])
            cp, sp = np.cos(euler[1]), np.sin(euler[1])
            cy, sy = np.cos(euler[2]), np.sin(euler[2])
            
            # Body Z vector in world frame
            R_z = np.array([
                sp * cy + sr * cp * sy,
                sp * sy - sr * cp * cy,
                cp * cr
            ])
            
            thrust_world = R_z * total_thrust
            accel_x = (thrust_world[0] - DRAG_COEFF * vel[0]) / MASS
            accel_y = (thrust_world[1] - DRAG_COEFF * vel[1]) / MASS
            accel_z = (thrust_world[2] - MASS * GRAVITY - DRAG_COEFF * vel[2]) / MASS
            
            vel[0] += accel_x * DT
            vel[1] += accel_y * DT
            vel[2] += accel_z * DT
            
            pos[0] += vel[0] * DT
            pos[1] += vel[1] * DT
            pos[2] += vel[2] * DT
            
    np.savez_compressed(
        save_path,
        observations=obs_list,
        actions=act_list
    )
    duration = time.time() - start_t
    print(f"Done in {duration:.2f}s! Saved {total_steps:,} closed-loop trajectory steps.")

if __name__ == '__main__':
    simulate_trajectory_dataset()
