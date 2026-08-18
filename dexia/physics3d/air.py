"""FixedWing3DOFEngine — point-mass aircraft that flies itself.

The standard constructive-sim flight model: a point mass with aerodynamic
lift/drag, thrust, and a bank-to-turn autopilot. Given a target it computes its
*own* heading, altitude and speed within real performance limits:

  heading   coordinated turn   chi_dot = (g / V) tan(mu)          (bank mu)
  speed     thrust vs drag     V_dot   = (T - D)/m - g sin(gamma)
  altitude  flight-path angle  h_dot   = V sin(gamma)             (gamma autopilot)
  drag      parabolic polar    D = q S (C_D0 + k C_L^2),  q = 1/2 rho V^2
  load      turn load factor   n = 1/cos(mu)   (caps turn by C_Lmax / structure)

The numpy workhorse for many aircraft at scale; a JSBSim 6-DOF engine (P2) can
replace it behind the same interface for hero aircraft.
"""

from __future__ import annotations

import math

import numpy as np

from .integrate import clamp, wrap_angle
from .state import Body6, quat_from_euler

G = 9.80665


def isa_density(h: float) -> float:
    """ISA troposphere air density [kg/m^3] (valid to ~11 km)."""
    h = max(0.0, min(11000.0, h))
    return 1.225 * (1.0 - 2.25577e-5 * h) ** 4.2559


class FixedWing3DOFEngine:
    def __init__(self, *, mass: float = 12000.0, wing_area: float = 28.0,
                 cd0: float = 0.022, k_induced: float = 0.045, t_max: float = 76000.0,
                 v_stall: float = 65.0, v_max: float = 320.0, cruise: float = 200.0,
                 bank_max_deg: float = 60.0, gamma_max_deg: float = 20.0,
                 k_heading: float = 2.5, k_gamma: float = 0.6, k_speed: float = 0.5,
                 gamma_rate_max_deg: float = 10.0) -> None:
        self.m, self.S = mass, wing_area
        self.cd0, self.k = cd0, k_induced
        self.t_max = t_max
        self.v_stall, self.v_max, self.cruise = v_stall, v_max, cruise
        self.mu_max = math.radians(bank_max_deg)
        self.gamma_max = math.radians(gamma_max_deg)
        self.gamma_rate_max = math.radians(gamma_rate_max_deg)
        self.kpsi, self.kgam, self.kv = k_heading, k_gamma, k_speed
        # state
        self.x = self.y = self.h = 0.0
        self.V = cruise
        self.chi = 0.0          # heading [rad]
        self.gamma = 0.0        # flight-path angle [rad]
        self.mu = 0.0           # bank [rad]
        self.target = None
        self.v_cmd = cruise

    def reset(self, pos3, V: float | None = None, heading: float = 0.0,
              gamma: float = 0.0) -> Body6:
        self.x, self.y, self.h = float(pos3[0]), float(pos3[1]), float(pos3[2])
        self.V = float(V if V is not None else self.cruise)
        self.chi, self.gamma, self.mu = float(heading), float(gamma), 0.0
        return self.state()

    def set_target(self, pos3, speed: float | None = None) -> None:
        self.target = np.asarray(pos3, float)
        if speed is not None:
            self.v_cmd = float(speed)

    # ------------------------------------------------------------------ #
    def step(self, dt: float) -> Body6:
        # --- guidance: turn toward the target, climb/descend to its altitude --
        if self.target is not None:
            chi_des = math.atan2(self.target[1] - self.y, self.target[0] - self.x)
            e_chi = wrap_angle(chi_des - self.chi)
            self.mu = clamp(self.kpsi * e_chi, -self.mu_max, self.mu_max)
            h_err = self.target[2] - self.h
            gamma_des = clamp(math.atan2(self.kgam * h_err, self.V),
                              -self.gamma_max, self.gamma_max)
        else:
            self.mu = 0.0
            gamma_des = 0.0

        # --- attitude/altitude rates -----------------------------------------
        chi_dot = (G / max(self.V, 1.0)) * math.tan(self.mu)     # coordinated turn
        gamma_dot = clamp(self.kgam * (gamma_des - self.gamma),
                          -self.gamma_rate_max, self.gamma_rate_max)

        # --- forces: parabolic drag polar at the turn load factor ------------
        rho = isa_density(self.h)
        q = 0.5 * rho * self.V * self.V
        n = 1.0 / max(0.2, math.cos(self.mu))                    # load factor in the turn
        cl = (n * self.m * G) / max(1.0, q * self.S)
        cd = self.cd0 + self.k * cl * cl
        drag = q * self.S * cd
        # thrust autopilot: hold v_cmd, overcoming drag + the climb component
        thrust = clamp(self.kv * (self.v_cmd - self.V) * self.m + drag + self.m * G * math.sin(self.gamma),
                       0.0, self.t_max)
        v_dot = (thrust - drag) / self.m - G * math.sin(self.gamma)

        # --- integrate (semi-implicit) ---------------------------------------
        self.V = max(self.v_stall, self.V + v_dot * dt)
        self.gamma += gamma_dot * dt
        self.chi = wrap_angle(self.chi + chi_dot * dt)
        self.x += self.V * math.cos(self.gamma) * math.cos(self.chi) * dt
        self.y += self.V * math.cos(self.gamma) * math.sin(self.chi) * dt
        self.h += self.V * math.sin(self.gamma) * dt
        return self.state()

    @property
    def load_factor(self) -> float:
        return 1.0 / max(0.2, math.cos(self.mu))

    def turn_radius(self) -> float:
        t = math.tan(self.mu)
        return float("inf") if abs(t) < 1e-6 else (self.V * self.V) / (G * t)

    # ------------------------------------------------------------------ #
    def state(self) -> Body6:
        cg = math.cos(self.gamma)
        return Body6(
            pos=np.array([self.x, self.y, self.h]),
            vel=np.array([self.V * cg * math.cos(self.chi),
                          self.V * cg * math.sin(self.chi),
                          self.V * math.sin(self.gamma)]),
            quat=quat_from_euler(-self.mu, self.gamma, self.chi),   # roll=-bank
        )
