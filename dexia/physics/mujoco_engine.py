"""MuJoCo 6-DOF quadcopter physics backend (Phase 2).

Implements the :class:`PhysicsEngine` ABC using the **official** ``mujoco``
Python bindings (not the deprecated ``mujoco-py``), so it runs on Windows +
Python 3.13 with prebuilt wheels and no compilation.

The drone is an "X" quadcopter: a central rigid body with a free joint (6-DOF)
and four motor actuators. Each actuator applies a thrust force along the body
+z axis at its rotor site and a reaction yaw torque (alternating sign per rotor
spin direction). This reproduces the real coupling that makes quad control
non-trivial:

    * differential front/back thrust  -> pitch
    * differential left/right thrust   -> roll
    * differential CW/CCW reaction     -> yaw
    * collective thrust                -> climb / descent

The agent commands 4 continuous actions in ``[-1, 1]`` (one per motor),
linearly mapped to ``[0, max_thrust]`` Newtons — i.e. normalised PWM / throttle.
"""

from __future__ import annotations

import numpy as np

import mujoco

from .base import DroneState6DOF, PhysicsEngine
from .mujoco_builder import DEFAULT_PROFILE, generate_mjcf, normalize_profile

# --------------------------------------------------------------------------- #
# Quadcopter MJCF model.
#
#   - mass ~0.7 kg, arm length 0.11 m
#   - 4 motors, each ctrlrange 0..7 N  -> max collective 28 N (~4:1 T/W)
#   - hover thrust per motor ~= 0.7*9.81/4 ~= 1.72 N
#   - yaw reaction coefficient kappa = 0.0201 m  (torque = kappa * thrust)
# --------------------------------------------------------------------------- #
_QUAD_XML = """
<mujoco model="dexia_quad">
  <compiler inertiafromgeom="true" coordinate="local"/>
  <option timestep="0.004" gravity="0 0 -9.81" density="1.2" integrator="RK4"/>

  <default>
    <geom condim="3"/>
  </default>

  <worldbody>
    <light pos="0 0 3" dir="0 0 -1"/>
    <geom name="floor" type="plane" pos="0 0 0" size="20 20 0.1" rgba="0.3 0.3 0.3 1"/>

    <body name="core" pos="0 0 1.0">
      <freejoint name="root"/>
      <geom name="core" type="box" size="0.06 0.06 0.015" mass="0.4" rgba="0.2 0.3 0.8 1"/>
      <geom name="arm_fr" type="capsule" fromto="0 0 0  0.11  0.11 0" size="0.008" mass="0.05"/>
      <geom name="arm_fl" type="capsule" fromto="0 0 0 -0.11  0.11 0" size="0.008" mass="0.05"/>
      <geom name="arm_bl" type="capsule" fromto="0 0 0 -0.11 -0.11 0" size="0.008" mass="0.05"/>
      <geom name="arm_br" type="capsule" fromto="0 0 0  0.11 -0.11 0" size="0.008" mass="0.05"/>

      <!-- rotor sites: X-config. m0 FR, m1 FL, m2 BL, m3 BR -->
      <site name="m0" pos=" 0.11  0.11 0.015" size="0.01"/>
      <site name="m1" pos="-0.11  0.11 0.015" size="0.01"/>
      <site name="m2" pos="-0.11 -0.11 0.015" size="0.01"/>
      <site name="m3" pos=" 0.11 -0.11 0.015" size="0.01"/>
    </body>
  </worldbody>

  <actuator>
    <!-- gear = (fx fy fz tx ty tz) applied at the site in body frame.
         thrust along +z, yaw reaction torque alternates sign. -->
    <motor name="t0" site="m0" gear="0 0 1 0 0  0.0201" ctrlrange="0 7"/>
    <motor name="t1" site="m1" gear="0 0 1 0 0 -0.0201" ctrlrange="0 7"/>
    <motor name="t2" site="m2" gear="0 0 1 0 0  0.0201" ctrlrange="0 7"/>
    <motor name="t3" site="m3" gear="0 0 1 0 0 -0.0201" ctrlrange="0 7"/>
  </actuator>
</mujoco>
"""


def _quat_to_euler(q: np.ndarray) -> np.ndarray:
    """Convert quaternion [w, x, y, z] to Euler [roll, pitch, yaw] (rad)."""
    w, x, y, z = q
    # roll (x-axis)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = np.arctan2(sinr_cosp, cosr_cosp)
    # pitch (y-axis)
    sinp = 2.0 * (w * y - z * x)
    pitch = np.arcsin(np.clip(sinp, -1.0, 1.0))
    # yaw (z-axis)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = np.arctan2(siny_cosp, cosy_cosp)
    return np.array([roll, pitch, yaw], dtype=np.float64)


class MuJoCoQuadEngine(PhysicsEngine):
    """6-DOF multirotor backed by MuJoCo, built from a Drone Garage profile.

    The model is generated dynamically from a profile dict (topology, mass, arm
    length, thrust, drag) via :func:`generate_mjcf`, or supplied directly as an
    MJCF ``xml_string``. The number of motors (``N_MOTORS``) follows the model's
    actuator count, so quad / hexa / tandem airframes all work through the same
    engine and the same :class:`PhysicsEngine` ABC.
    """

    def __init__(
        self,
        profile: dict | None = None,
        xml_string: str | None = None,
        control_decimation: int = 5,
        max_thrust: float | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Parameters
        ----------
        profile : Drone Garage profile dict (topology/mass/arm_length/...).
            Defaults to a standard quad reproducing the original Phase-2 model.
        xml_string : a ready MJCF string. Takes precedence over ``profile``.
        control_decimation : MuJoCo sub-steps per ``step`` call (0.004 s * 5
            -> 50 Hz control).
        max_thrust : optional override for the per-motor de-normalisation thrust
            (otherwise taken from the profile).
        """
        if xml_string is not None:
            self.profile = None
            self.drag_coeff = 0.0
            xml = xml_string
            prof_thrust = max_thrust if max_thrust is not None else 7.0
        else:
            self.profile = normalize_profile(profile)
            self.drag_coeff = float(self.profile["drag_coeff"])
            xml = generate_mjcf(self.profile)
            prof_thrust = self.profile["max_thrust"]

        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        self.control_decimation = int(control_decimation)
        self.max_thrust = float(max_thrust if max_thrust is not None else prof_thrust)
        self.dt = float(self.model.opt.timestep) * self.control_decimation
        self._rng = np.random.default_rng(seed)

        # Number of motors follows the compiled model (quad=4, hexa=6, ...).
        self.N_MOTORS = int(self.model.nu)

        # Cache the free-joint qpos/qvel addresses.
        self._qpos_adr = self.model.jnt_qposadr[0]
        self._qvel_adr = self.model.jnt_dofadr[0]
        # Body index that carries the airframe (first non-world body).
        self._body_idx = 1

        # Per-motor hover thrust (for reference / shaping).
        total_mass = float(np.sum(self.model.body_mass))
        self.hover_thrust = total_mass * 9.81 / max(self.N_MOTORS, 1)
        self.total_mass = total_mass

    # ------------------------------------------------------------------ #
    # PhysicsEngine interface
    # ------------------------------------------------------------------ #
    def reset(
        self,
        position: np.ndarray,
        velocity: np.ndarray | None = None,
        orientation: np.ndarray | None = None,
        angular_velocity: np.ndarray | None = None,
    ) -> DroneState6DOF:
        """Reset to a full 6-DOF state.

        ``orientation`` is Euler [roll, pitch, yaw] in radians (defaults to
        level). Extra kwargs beyond the ABC signature are optional, so this
        stays substitutable for a 3-DOF engine.
        """
        mujoco.mj_resetData(self.model, self.data)

        pos = np.asarray(position, dtype=np.float64).reshape(3)
        vel = np.zeros(3) if velocity is None else np.asarray(velocity, dtype=np.float64).reshape(3)
        euler = (
            np.zeros(3) if orientation is None
            else np.asarray(orientation, dtype=np.float64).reshape(3)
        )
        ang = (
            np.zeros(3) if angular_velocity is None
            else np.asarray(angular_velocity, dtype=np.float64).reshape(3)
        )

        quat = np.zeros(4)
        mujoco.mju_euler2Quat(quat, euler, "xyz")

        qpos = self.data.qpos
        qpos[self._qpos_adr : self._qpos_adr + 3] = pos
        qpos[self._qpos_adr + 3 : self._qpos_adr + 7] = quat

        qvel = self.data.qvel
        qvel[self._qvel_adr : self._qvel_adr + 3] = vel
        qvel[self._qvel_adr + 3 : self._qvel_adr + 6] = ang

        mujoco.mj_forward(self.model, self.data)
        return self.get_state()

    def step(self, action: np.ndarray, external_force: np.ndarray | None = None) -> DroneState6DOF:
        """Advance one control step.

        Parameters
        ----------
        action : np.ndarray shape (4,)
            Per-motor command in [-1, 1], mapped to [0, max_thrust] Newtons.
        external_force : np.ndarray shape (3,), optional
            World-frame disturbance force [N] applied to the core body
            (e.g. wind in later phases). Disabled (None) during Phase 2.
        """
        action = np.asarray(action, dtype=np.float64).reshape(self.N_MOTORS)
        # Map [-1, 1] -> [0, max_thrust]; clip for safety.
        ctrl = (np.clip(action, -1.0, 1.0) + 1.0) * 0.5 * self.max_thrust
        self.data.ctrl[:] = ctrl

        # Combined body force: external disturbance (e.g. wind) + linear aero
        # drag (-c * v) from the profile's drag coefficient.
        f = np.zeros(3, dtype=np.float64)
        if external_force is not None:
            f += np.asarray(external_force, dtype=np.float64).reshape(3)
        if self.drag_coeff > 0.0:
            v_lin = np.asarray(
                self.data.qvel[self._qvel_adr : self._qvel_adr + 3], dtype=np.float64
            )
            f += -self.drag_coeff * v_lin

        self.data.xfrc_applied[:] = 0.0
        self.data.xfrc_applied[self._body_idx, :3] = f

        for _ in range(self.control_decimation):
            mujoco.mj_step(self.model, self.data)

        return self.get_state()

    def get_state(self) -> DroneState6DOF:
        qpos = self.data.qpos
        qvel = self.data.qvel
        a, va = self._qpos_adr, self._qvel_adr

        pos = np.array(qpos[a : a + 3], dtype=np.float64)
        quat = np.array(qpos[a + 3 : a + 7], dtype=np.float64)
        vel = np.array(qvel[va : va + 3], dtype=np.float64)
        ang = np.array(qvel[va + 3 : va + 6], dtype=np.float64)
        euler = _quat_to_euler(quat)

        return DroneState6DOF(
            position=pos,
            velocity=vel,
            orientation=euler,
            angular_velocity=ang,
            quaternion=quat,
        )

    # ------------------------------------------------------------------ #
    @property
    def hover_action(self) -> np.ndarray:
        """The normalised 4-action that produces hover (collective = weight)."""
        a = 2.0 * (self.hover_thrust / self.max_thrust) - 1.0
        return np.full(self.N_MOTORS, a, dtype=np.float64)
