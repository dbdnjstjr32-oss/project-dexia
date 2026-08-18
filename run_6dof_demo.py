"""
6-DOF Quadcopter Flight Dynamics Simulation & 3D Interactive Visualizer
Powered by Dexia's MuJoCo 6-DOF Physics Engine
"""

import math
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dexia.physics.mujoco_engine import MuJoCoQuadEngine
from dexia.physics3d.state import quat_to_euler

def geometric_position_controller(pos, vel, euler, omega, target_pos, target_yaw=0.0, mass=0.6, gravity=9.81, max_thrust=7.0, arm_length=0.11, kappa=0.0201):
    """
    Exact Analytical Newton-Euler Geometric Controller with Thrust Priority Allocation.
    """
    roll, pitch, yaw = euler
    
    # 1. Outer Loop: 3D Position & Velocity Tracking
    kp_pos = np.array([4.0, 4.0, 6.0])
    kd_pos = np.array([3.0, 3.0, 4.5])
    
    pos_err = target_pos - pos
    vel_err = -vel
    
    # Commanded world acceleration [ax, ay, az]
    a_cmd = kp_pos * pos_err + kd_pos * vel_err
    a_cmd[2] += gravity
    
    # 2. Total Collective Thrust
    cos_tilt = max(math.cos(roll) * math.cos(pitch), 0.7)
    thrust_total = (mass * a_cmd[2]) / cos_tilt
    thrust_total = np.clip(thrust_total, 0.5, 4 * max_thrust * 0.75)
    
    # 3. Attitude Planning
    cy, sy = math.cos(yaw), math.sin(yaw)
    a_world_x, a_world_y = a_cmd[0], a_cmd[1]
    
    # Body-frame desired accelerations
    a_body_fwd = -a_world_x * sy + a_world_y * cy # +y
    a_body_right = a_world_x * cy + a_world_y * sy # +x
    
    # Tilt angles (limited to safe 25 degrees)
    roll_des = np.clip(-a_body_fwd / gravity, -math.radians(25), math.radians(25))
    pitch_des = np.clip(a_body_right / gravity, -math.radians(25), math.radians(25))
    yaw_des = target_yaw
    
    # 4. Inner Loop: Attitude & Angular Velocity PD Controller
    kp_att = np.array([12.0, 12.0, 5.0])
    kd_att = np.array([1.8, 1.8, 0.9])
    
    e_roll = roll_des - roll
    e_pitch = pitch_des - pitch
    e_yaw = (yaw_des - yaw + np.pi) % (2 * np.pi) - np.pi
    
    tau_cmd = kp_att * np.array([e_roll, e_pitch, e_yaw]) - kd_att * omega
    
    # 5. Control Allocation with Thrust Prioritization
    L = arm_length
    t_base = thrust_total / 4.0
    
    # Limit differential thrusts so base thrust is not severely perturbed
    max_d_att = 0.6 * t_base
    dt_x = np.clip(tau_cmd[0] / (4.0 * L), -max_d_att, max_d_att)
    dt_y = np.clip(tau_cmd[1] / (4.0 * L), -max_d_att, max_d_att)
    dt_z = np.clip(tau_cmd[2] / (4.0 * kappa), -0.3 * t_base, 0.3 * t_base)
    
    T0 = t_base + dt_x - dt_y + dt_z
    T1 = t_base + dt_x + dt_y - dt_z
    T2 = t_base - dt_x + dt_y + dt_z
    T3 = t_base - dt_x - dt_y - dt_z
    
    thrusts = np.clip([T0, T1, T2, T3], 0.0, max_thrust)
    
    # Normalized command [-1, 1]
    actions = 2.0 * (thrusts / max_thrust) - 1.0
    return np.clip(actions, -1.0, 1.0), thrusts, (roll_des, pitch_des, yaw_des)

def run_simulation():
    print("======================================================================")
    print("DEXIA 6-DOF QUADCOPTER FLIGHT SIMULATION (MuJoCo Physics)")
    print("======================================================================")
    
    engine = MuJoCoQuadEngine(control_decimation=5)
    dt = engine.dt # 0.02s (50 Hz)
    
    # Initial State (Spawn at origin, height 0.5m)
    state = engine.reset(position=np.array([0.0, 0.0, 0.5]), orientation=np.array([0.0, 0.0, 0.0]))
    
    # Waypoint Sequence (Takeoff -> 3D Square / Slalom Course -> Return to Hover)
    waypoints = [
        {"time": 0.0, "pos": np.array([0.0, 0.0, 2.0]), "yaw": 0.0, "label": "Takeoff & Climb"},
        {"time": 3.0, "pos": np.array([3.0, 0.0, 2.5]), "yaw": math.radians(30), "label": "Waypoint 1 (East Forward)"},
        {"time": 6.0, "pos": np.array([3.0, 3.0, 3.0]), "yaw": math.radians(90), "label": "Waypoint 2 (North Climb)"},
        {"time": 9.0, "pos": np.array([0.0, 3.0, 2.5]), "yaw": math.radians(180), "label": "Waypoint 3 (West Turn)"},
        {"time": 12.0, "pos": np.array([0.0, 0.0, 2.0]), "yaw": math.radians(0), "label": "Waypoint 4 (Return Home)"},
        {"time": 15.0, "pos": np.array([0.0, 0.0, 1.5]), "yaw": math.radians(0), "label": "Precision Hover & Hold"},
    ]
    
    total_time = 18.0
    n_steps = int(total_time / dt)
    
    # Logs
    times = []
    positions = []
    velocities = []
    orientations = []
    omegas = []
    motor_thrusts = []
    target_positions = []
    target_orientations = []
    
    print(f"Total duration : {total_time:.1f} s ({n_steps} steps at {1/dt:.0f} Hz)")
    print(f"Airframe mass  : {engine.total_mass:.3f} kg | Hover thrust/motor: {engine.hover_thrust:.2f} N")
    print("\nSimulating flight trajectory...")
    
    for step in range(n_steps):
        t = step * dt
        
        # Determine current active target waypoint
        current_wp = waypoints[0]
        for wp in waypoints:
            if t >= wp["time"]:
                current_wp = wp
        target_p = current_wp["pos"]
        target_y = current_wp["yaw"]
        
        # Current 6-DOF state
        pos = state.position
        vel = state.velocity
        euler = state.orientation
        omega = state.angular_velocity
        
        # Compute control action
        action, thrusts, des_att = geometric_position_controller(
            pos, vel, euler, omega,
            target_pos=target_p,
            target_yaw=target_y,
            mass=engine.total_mass,
            gravity=9.81,
            max_thrust=engine.max_thrust,
            arm_length=0.11,
            kappa=0.0201
        )
        
        # Inject realistic moderate wind disturbance during step 200~400
        ext_force = np.zeros(3)
        if 4.0 <= t <= 7.0:
            ext_force = np.array([0.8 * math.sin(2 * np.pi * t), 0.5, 0.2])
            
        # Step MuJoCo 6-DOF physics
        state = engine.step(action=action, external_force=ext_force)
        
        # Record telemetry
        times.append(t)
        positions.append(pos.copy())
        velocities.append(vel.copy())
        orientations.append(euler.copy())
        omegas.append(omega.copy())
        motor_thrusts.append(thrusts.copy())
        target_positions.append(target_p.copy())
        target_orientations.append(np.array(des_att))
        
        if step % 100 == 0 or step == n_steps - 1:
            print(f"  [t={t:5.2f}s] Pos=[{pos[0]:5.2f}, {pos[1]:5.2f}, {pos[2]:5.2f}] m | Roll={math.degrees(euler[0]):5.1f} deg Pitch={math.degrees(euler[1]):5.1f} deg Yaw={math.degrees(euler[2]):5.1f} deg | Thrust={np.sum(thrusts):4.2f} N")

    positions = np.array(positions)
    velocities = np.array(velocities)
    orientations = np.array(orientations)
    motor_thrusts = np.array(motor_thrusts)
    target_positions = np.array(target_positions)
    
    print("\n[OK] Simulation completed successfully!")
    print(f"Final Drone Position: [{positions[-1, 0]:.3f}, {positions[-1, 1]:.3f}, {positions[-1, 2]:.3f}] m")
    
    # -------------------------------------------------------------
    # Build Interactive HTML 3D & Telemetry Dashboard
    # -------------------------------------------------------------
    print("\nBuilding Interactive 3D & Telemetry Dashboard (Plotly)...")
    
    fig = make_subplots(
        rows=3, cols=2,
        column_widths=[0.55, 0.45],
        row_heights=[0.35, 0.35, 0.30],
        specs=[
            [{"type": "scatter3d", "rowspan": 3}, {"type": "xy"}],
            [None, {"type": "xy"}],
            [None, {"type": "xy"}]
        ],
        subplot_titles=[
            "<b>3D 6-DOF Flight Trajectory & Waypoints</b>",
            "<b>Altitude & 3D Positions vs Time</b>",
            "<b>Attitude Angles (Roll / Pitch / Yaw)</b>",
            "<b>Individual Motor Thrusts (T0 ~ T3)</b>"
        ]
    )
    
    # 1. 3D Trajectory
    fig.add_trace(
        go.Scatter3d(
            x=positions[:, 0], y=positions[:, 1], z=positions[:, 2],
            mode="lines",
            line=dict(color=times, colorscale="Viridis", width=6),
            name="Drone 6-DOF Path"
        ),
        row=1, col=1
    )
    
    # Waypoint markers
    wp_pts = np.array([wp["pos"] for wp in waypoints])
    wp_texts = [f"WP: {wp['label']}<br>Target: {wp['pos']}" for wp in waypoints]
    fig.add_trace(
        go.Scatter3d(
            x=wp_pts[:, 0], y=wp_pts[:, 1], z=wp_pts[:, 2],
            mode="markers+text",
            marker=dict(size=8, color="red", symbol="diamond"),
            text=[f"WP {i}" for i in range(len(waypoints))],
            textposition="top center",
            hovertext=wp_texts,
            name="Waypoints"
        ),
        row=1, col=1
    )
    
    # Start and End points
    fig.add_trace(
        go.Scatter3d(
            x=[positions[0, 0]], y=[positions[0, 1]], z=[positions[0, 2]],
            mode="markers",
            marker=dict(size=10, color="green", symbol="circle"),
            name="Spawn"
        ),
        row=1, col=1
    )
    fig.add_trace(
        go.Scatter3d(
            x=[positions[-1, 0]], y=[positions[-1, 1]], z=[positions[-1, 2]],
            mode="markers",
            marker=dict(size=10, color="blue", symbol="square"),
            name="Final Position"
        ),
        row=1, col=1
    )
    
    # 2. Position vs Time
    fig.add_trace(go.Scatter(x=times, y=positions[:, 0], name="Pos X (m)", line=dict(color="#1f77b4")), row=1, col=2)
    fig.add_trace(go.Scatter(x=times, y=positions[:, 1], name="Pos Y (m)", line=dict(color="#ff7f0e")), row=1, col=2)
    fig.add_trace(go.Scatter(x=times, y=positions[:, 2], name="Altitude Z (m)", line=dict(color="#2ca02c", width=3)), row=1, col=2)
    fig.add_trace(go.Scatter(x=times, y=target_positions[:, 2], name="Target Alt (m)", line=dict(color="black", dash="dash")), row=1, col=2)
    
    # 3. Attitude (Roll / Pitch / Yaw)
    fig.add_trace(go.Scatter(x=times, y=np.degrees(orientations[:, 0]), name="Roll phi (deg)", line=dict(color="#d62728")), row=2, col=2)
    fig.add_trace(go.Scatter(x=times, y=np.degrees(orientations[:, 1]), name="Pitch theta (deg)", line=dict(color="#9467bd")), row=2, col=2)
    fig.add_trace(go.Scatter(x=times, y=np.degrees(orientations[:, 2]), name="Yaw psi (deg)", line=dict(color="#8c564b")), row=2, col=2)
    
    # 4. Motor Thrusts
    fig.add_trace(go.Scatter(x=times, y=motor_thrusts[:, 0], name="T0 Front-Right (N)", line=dict(color="#e377c2")), row=3, col=2)
    fig.add_trace(go.Scatter(x=times, y=motor_thrusts[:, 1], name="T1 Front-Left (N)", line=dict(color="#7f7f7f")), row=3, col=2)
    fig.add_trace(go.Scatter(x=times, y=motor_thrusts[:, 2], name="T2 Back-Left (N)", line=dict(color="#bcbd22")), row=3, col=2)
    fig.add_trace(go.Scatter(x=times, y=motor_thrusts[:, 3], name="T3 Back-Right (N)", line=dict(color="#17becf")), row=3, col=2)
    fig.add_trace(go.Scatter(x=times, y=[engine.hover_thrust]*len(times), name="Hover Thrust Level", line=dict(color="gray", dash="dot")), row=3, col=2)
    
    fig.update_layout(
        title="<b>DEXIA 6-DOF QUADCOPTER FLIGHT DYNAMICS SIMULATION</b><br><sup>MuJoCo RK4 Integration · 3D Trajectory & Real-time Telemetry Dashboard</sup>",
        template="plotly_dark",
        height=950,
        scene=dict(
            xaxis_title="East (m)",
            yaxis_title="North (m)",
            zaxis_title="Altitude (m)",
            aspectmode="cube"
        ),
        legend=dict(orientation="h", yanchor="bottom", y=-0.08, xanchor="center", x=0.5)
    )
    
    out_file = "dof6_simulation_dashboard.html"
    fig.write_html(out_file)
    print(f"[OK] Interactive Dashboard saved to: {out_file}")
    return out_file

if __name__ == "__main__":
    run_simulation()
