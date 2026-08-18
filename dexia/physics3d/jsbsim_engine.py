"""JSBSimEngine — optional 6-DOF flight dynamics for a hero aircraft (tier B / P2).

A real JSBSim FDM behind the *same* interface as the numpy ``FixedWing3DOFEngine``
(reset / set_target / step -> Body6, plus .x/.y/.chi), so it drops straight into
the P3 ``MotionModel`` air path. JSBSim is an *optional* dependency: if it can't be
imported, ``make_air_engine`` transparently falls back to the numpy model, so a
machine without jsbsim still runs everything.

A light waypoint autopilot (bank-to-turn + altitude-hold + speed-hold P-loops on
the aircraft's own control surfaces) flies a bundled airframe (default c172x) to
the commanded target. World frame is local ENU [East, North, Up] in metres,
anchored at the reset position; JSBSim's ``distance-from-start`` + ``h-sl`` give us
that directly, and Euler attitude maps to our quaternion via state.py.
"""

from __future__ import annotations

import math

import numpy as np

from .integrate import clamp, wrap_angle
from .state import Body6, quat_from_euler

try:                       # optional dependency — degrade gracefully without it
    import jsbsim
    JSBSIM_AVAILABLE = True
except Exception:          # pragma: no cover - import guard
    jsbsim = None
    JSBSIM_AVAILABLE = False

FT2M = 0.3048
KTS_PER_MPS = 1.943844


class JSBSimEngine:
    """6-DOF fixed-wing on a JSBSim airframe, API-compatible with FixedWing3DOFEngine."""

    def __init__(self, *, aircraft: str = "c172x", cruise: float = 50.0,
                 k_bank: float = 0.8, k_pitch: float = 0.3, k_speed: float = 0.03,
                 bank_max_deg: float = 25.0) -> None:
        if not JSBSIM_AVAILABLE:                       # pragma: no cover - guarded by factory
            raise RuntimeError("jsbsim not available")
        self.fdm = jsbsim.FGFDMExec(None)
        self.fdm.set_debug_level(0)
        if not self.fdm.load_model(aircraft):
            raise RuntimeError(f"jsbsim could not load aircraft '{aircraft}'")
        self.cruise = float(cruise)
        self.k_bank, self.k_pitch, self.k_speed = k_bank, k_pitch, k_speed
        self.bank_max = math.radians(bank_max_deg)
        self.dt = self.fdm.get_delta_t()
        # ENU origin (anchored at reset) + cached pose for MotionModel
        self.x0 = self.y0 = 0.0
        self.x = self.y = self.h = 0.0
        self.chi = 0.0
        self.v_cmd = self.cruise
        self.target = None
        self.e_trim = 0.0        # elevator/throttle that hold level flight at IC
        self.t_trim = 0.6

    # ------------------------------------------------------------------ #
    def reset(self, pos3, V: float | None = None, heading: float = 0.0,
              gamma: float = 0.0) -> Body6:
        self.x0, self.y0 = float(pos3[0]), float(pos3[1])
        h0 = float(pos3[2]) if len(pos3) > 2 else 0.0
        v = float(V if V is not None else self.cruise)
        self.v_cmd = v
        f = self.fdm
        try:                                            # piston engine must be cranked
            f.get_propulsion().init_running(-1)
        except Exception:                               # pragma: no cover
            pass
        f["ic/lat-gc-deg"] = 0.0
        f["ic/long-gc-deg"] = 0.0
        f["ic/h-sl-ft"] = h0 / FT2M
        # ENU course (0=East, CCW) -> JSBSim compass heading (0=North, CW)
        f["ic/psi-true-deg"] = (90.0 - math.degrees(heading)) % 360.0
        f["ic/gamma-deg"] = math.degrees(gamma)
        f["ic/vc-kts"] = v * KTS_PER_MPS
        f.run_ic()
        try:                                            # trim to equilibrium level flight
            f["simulation/do_simple_trim"] = 1
        except Exception:                               # pragma: no cover
            pass
        self.e_trim = float(f["fcs/elevator-cmd-norm"])
        self.t_trim = float(f["fcs/throttle-cmd-norm"])
        self._sync()
        return self.state()

    def set_target(self, pos3, speed: float | None = None) -> None:
        self.target = np.asarray(pos3, dtype=np.float64)
        if speed is not None:
            self.v_cmd = float(speed)

    # ------------------------------------------------------------------ #
    def _sync(self) -> None:
        f = self.fdm
        self.x = self.x0 + float(f["position/distance-from-start-lon-mt"])
        self.y = self.y0 + float(f["position/distance-from-start-lat-mt"])
        self.h = float(f["position/h-sl-meters"])
        # ENU course (0 = East, CCW), so it matches FixedWing3DOFEngine.chi and the
        # autopilot's atan2(dy, dx) target bearing — NOT the JSBSim compass psi.
        ve = float(f["velocities/v-east-fps"])
        vn = float(f["velocities/v-north-fps"])
        self.chi = math.atan2(vn, ve)

    def _autopilot(self) -> None:
        f = self.fdm
        phi = float(f["attitude/phi-rad"])
        theta = float(f["attitude/theta-rad"])
        if self.target is not None:
            chi_des = math.atan2(self.target[1] - self.y, self.target[0] - self.x)
            e_chi = wrap_angle(chi_des - self.chi)
            bank = clamp(self.k_bank * e_chi, -self.bank_max, self.bank_max)
            desired_phi = -bank          # JSBSim +phi (right wing down) turns ENU course CW (down)
            h_err = float(self.target[2]) - self.h
        else:
            desired_phi, h_err = 0.0, 0.0
        # roll-hold: aileron drives bank angle toward the commanded value
        f["fcs/aileron-cmd-norm"] = clamp(2.0 * (desired_phi - phi), -1.0, 1.0)
        # altitude-hold: a small elevator correction AROUND the trim setting — a
        # negative delta pitches up / climbs (verified for the c172x FDM)
        de = clamp(-self.k_pitch * math.atan2(h_err, 500.0), -0.18, 0.18)
        f["fcs/elevator-cmd-norm"] = clamp(self.e_trim + de, -1.0, 1.0)
        # speed-hold: throttle correction around the trim setting
        vt = float(f["velocities/vt-fps"]) * FT2M
        f["fcs/throttle-cmd-norm"] = clamp(self.t_trim + self.k_speed * (self.v_cmd - vt), 0.3, 1.0)
        f["fcs/mixture-cmd-norm"] = 1.0

    def step(self, dt: float) -> Body6:
        n = max(1, int(round(dt / self.dt)))
        for _ in range(n):
            self._autopilot()
            self.fdm.run()
        self._sync()
        return self.state()

    # ------------------------------------------------------------------ #
    def state(self) -> Body6:
        f = self.fdm
        ve = float(f["velocities/v-east-fps"]) * FT2M
        vn = float(f["velocities/v-north-fps"]) * FT2M
        vu = -float(f["velocities/v-down-fps"]) * FT2M
        roll = float(f["attitude/phi-rad"])
        pitch = float(f["attitude/theta-rad"])
        yaw = float(f["attitude/psi-rad"])
        return Body6(
            pos=np.array([self.x, self.y, self.h]),
            vel=np.array([ve, vn, vu]),
            quat=quat_from_euler(roll, pitch, yaw),
        )


# --------------------------------------------------------------------------- #
def make_air_engine(hero: bool, params: dict, *, cruise_alt: float = 0.0):
    """Pick the air engine: a JSBSim 6-DOF FDM for a hero aircraft when jsbsim is
    available, otherwise the numpy ``FixedWing3DOFEngine``. Always returns an engine
    with the same interface, so callers never branch."""
    from .air import FixedWing3DOFEngine
    if hero and JSBSIM_AVAILABLE:
        try:
            return JSBSimEngine(cruise=float(params.get("cruise", 50.0)))
        except Exception:                               # pragma: no cover - safety net
            pass
    return FixedWing3DOFEngine(**params)
