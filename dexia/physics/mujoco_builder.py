"""Dynamic MuJoCo (MJCF) model generator — the "Drone Garage" compiler.

Turns a lightweight *drone profile* (name, topology, mass, arm length, max motor
thrust, drag) into a valid MuJoCo MJCF XML string. This replaces the previously
hardcoded quad XML so airframes can be created/edited from the HUD and compiled
on demand by the Python backend.

Topologies
----------
    quad   : 4 motors in an X, evenly spaced on a ring of radius = arm_length
    hexa   : 6 motors evenly spaced on a ring of radius = arm_length
    tandem : 4 motors in a fore/aft rectangle (Tandem Tiltrotor VTOL layout) —
             elongated along the body x-axis (longer than wide)

Physics derivation
------------------
Every model carries an explicit ``<inertial>`` element whose **diagonal inertia
tensor** is computed mathematically (no reliance on the MuJoCo geom auto-inertia),
per the requirement. The airframe is approximated as:

    central hub  : solid cylinder (carries a fraction of the mass)
    motors+arms  : point masses at each rotor position (carry the remainder)

    I_xx = I_hub_xx + Σ m_i (y_i² + z_i²)
    I_yy = I_hub_yy + Σ m_i (x_i² + z_i²)
    I_zz = I_hub_zz + Σ m_i (x_i² + y_i²)

Each motor is a MuJoCo ``motor`` actuator applying thrust along body +z at its
site plus an alternating yaw-reaction torque (kappa), giving the real
collective/roll/pitch/yaw coupling.
"""

from __future__ import annotations

import math
from typing import Any

GRAVITY = 9.81
KAPPA = 0.0201          # yaw reaction torque per unit thrust [m]
HUB_MASS_FRACTION = 0.5  # fraction of total mass in the central hub
MOTOR_Z = 0.015         # rotor height above the hub plane [m]

VALID_TOPOLOGIES = ("quad", "hexa", "tandem")

# A sensible default that reproduces the original Phase-2 quad (~0.6 kg, 0.11 m).
DEFAULT_PROFILE: dict[str, Any] = {
    "name": "default_quad",
    "topology": "quad",
    "mass": 0.6,
    "arm_length": 0.11,
    "max_thrust": 7.0,
    "drag_coeff": 0.1,
}


def normalize_profile(profile: dict | None) -> dict[str, Any]:
    """Fill defaults, coerce types, and validate a raw profile dict."""
    p = dict(DEFAULT_PROFILE)
    if profile:
        p.update(profile)

    p["name"] = str(p.get("name", "drone")).strip() or "drone"
    topo = str(p.get("topology", "quad")).lower().strip()
    if topo not in VALID_TOPOLOGIES:
        raise ValueError(f"Unknown topology '{topo}'. Valid: {VALID_TOPOLOGIES}")
    p["topology"] = topo

    p["mass"] = max(float(p["mass"]), 0.05)
    p["arm_length"] = max(float(p["arm_length"]), 0.02)
    p["max_thrust"] = max(float(p["max_thrust"]), 0.1)
    p["drag_coeff"] = max(float(p.get("drag_coeff", 0.0)), 0.0)
    return p


def motor_layout(topology: str, arm_length: float) -> list[tuple[float, float, float, float]]:
    """Return motor positions + yaw-reaction sign: list of (x, y, z, sign).

    Signs alternate so that, at equal thrust, the net reaction yaw torque is
    balanced (sum of signs = 0 for the even motor counts used here).
    """
    r = float(arm_length)
    layout: list[tuple[float, float, float, float]] = []

    if topology == "quad":
        angles = [math.radians(a) for a in (45, 135, 225, 315)]
        for i, a in enumerate(angles):
            sign = 1.0 if i % 2 == 0 else -1.0
            layout.append((r * math.cos(a), r * math.sin(a), MOTOR_Z, sign))

    elif topology == "hexa":
        angles = [math.radians(60 * i) for i in range(6)]
        for i, a in enumerate(angles):
            sign = 1.0 if i % 2 == 0 else -1.0
            layout.append((r * math.cos(a), r * math.sin(a), MOTOR_Z, sign))

    elif topology == "tandem":
        # Fore/aft rectangle: elongated along x (fore-aft), narrower in y.
        fore = r                 # longitudinal half-spacing
        lat = r * 0.6            # lateral half-spacing
        # order: front-left, front-right, rear-left, rear-right
        pts = [(+fore, +lat), (+fore, -lat), (-fore, +lat), (-fore, -lat)]
        for i, (x, y) in enumerate(pts):
            sign = 1.0 if i % 2 == 0 else -1.0
            layout.append((x, y, MOTOR_Z, sign))

    else:  # pragma: no cover (validated upstream)
        raise ValueError(f"Unknown topology '{topology}'")

    return layout


def compute_diag_inertia(
    mass: float, arm_length: float, positions: list[tuple[float, float, float, float]]
) -> tuple[float, float, float]:
    """Mathematically derive the diagonal inertia tensor [Ixx, Iyy, Izz]."""
    n = len(positions)
    m_hub = HUB_MASS_FRACTION * mass
    m_motor = (1.0 - HUB_MASS_FRACTION) * mass / max(n, 1)

    # Central hub modelled as a solid cylinder (radius ~ 0.3 * arm, small height).
    r_hub = max(0.3 * arm_length, 1e-3)
    h_hub = 0.04
    ixx_hub = iyy_hub = (1.0 / 12.0) * m_hub * (3.0 * r_hub ** 2 + h_hub ** 2)
    izz_hub = 0.5 * m_hub * r_hub ** 2

    ixx, iyy, izz = ixx_hub, iyy_hub, izz_hub
    for (x, y, z, _sign) in positions:
        ixx += m_motor * (y * y + z * z)
        iyy += m_motor * (x * x + z * z)
        izz += m_motor * (x * x + y * y)

    eps = 1e-6
    return (max(ixx, eps), max(iyy, eps), max(izz, eps))


def generate_mjcf(profile: dict | None) -> str:
    """Build a valid MJCF XML string from a drone profile dict."""
    p = normalize_profile(profile)
    name = p["name"]
    topology = p["topology"]
    mass = p["mass"]
    arm = p["arm_length"]
    max_thrust = p["max_thrust"]

    positions = motor_layout(topology, arm)
    ixx, iyy, izz = compute_diag_inertia(mass, arm, positions)

    # Hub geom size (visual only — mass carried by the explicit <inertial>).
    hub_r = max(0.3 * arm, 0.03)
    hub_hx = hub_r * 0.8
    hub_hy = hub_r * (0.5 if topology == "tandem" else 0.8)  # tandem hub is longer in x
    hub_hx = hub_r * (1.3 if topology == "tandem" else 0.8)
    hub_hz = 0.015

    # XML-safe model name.
    safe_name = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name) or "drone"

    arms_xml = []
    sites_xml = []
    motors_xml = []
    for i, (x, y, z, sign) in enumerate(positions):
        arms_xml.append(
            f'      <geom name="arm_{i}" type="capsule" '
            f'fromto="0 0 0  {x:.5f} {y:.5f} 0" size="0.008" mass="0" rgba="0.6 0.6 0.6 1"/>'
        )
        sites_xml.append(f'      <site name="m{i}" pos="{x:.5f} {y:.5f} {z:.5f}" size="0.01"/>')
        motors_xml.append(
            f'    <motor name="t{i}" site="m{i}" '
            f'gear="0 0 1 0 0 {sign * KAPPA:+.5f}" ctrlrange="0 {max_thrust:.4f}"/>'
        )

    xml = f"""<mujoco model="{safe_name}">
  <compiler inertiafromgeom="auto" coordinate="local" angle="radian"/>
  <option timestep="0.004" gravity="0 0 {-GRAVITY}" density="1.2" integrator="RK4"/>

  <default>
    <geom condim="3"/>
  </default>

  <worldbody>
    <light pos="0 0 3" dir="0 0 -1"/>
    <geom name="floor" type="plane" pos="0 0 0" size="20 20 0.1" rgba="0.3 0.3 0.3 1"/>

    <body name="core" pos="0 0 1.0">
      <freejoint name="root"/>
      <inertial pos="0 0 0" mass="{mass:.5f}" diaginertia="{ixx:.8f} {iyy:.8f} {izz:.8f}"/>
      <geom name="hub" type="box" size="{hub_hx:.4f} {hub_hy:.4f} {hub_hz:.4f}" mass="0" rgba="0.2 0.3 0.8 1"/>
{chr(10).join(arms_xml)}
{chr(10).join(sites_xml)}
    </body>
  </worldbody>

  <actuator>
{chr(10).join(motors_xml)}
  </actuator>
</mujoco>
"""
    return xml


def profile_summary(profile: dict | None) -> dict[str, Any]:
    """Convenience: normalized profile + derived quantities (for inspection)."""
    p = normalize_profile(profile)
    positions = motor_layout(p["topology"], p["arm_length"])
    ixx, iyy, izz = compute_diag_inertia(p["mass"], p["arm_length"], positions)
    n = len(positions)
    return {
        **p,
        "n_motors": n,
        "diag_inertia": [ixx, iyy, izz],
        "hover_thrust_per_motor": p["mass"] * GRAVITY / n,
        "thrust_to_weight": (n * p["max_thrust"]) / (p["mass"] * GRAVITY),
    }
