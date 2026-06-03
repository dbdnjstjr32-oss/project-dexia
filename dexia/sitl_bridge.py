"""SITL (Software-In-The-Loop) bridge: RL policy <-> real flight controller.

Phase 4 preparation. This module is the *translator* between a learned policy
(an RLlib ``RLModule`` producing continuous actions in [-1, 1]) and a real-world
autopilot (PX4 / ArduPilot), which expects per-motor PWM signals in the standard
[1000, 2000] microsecond band.

It deliberately does **not** require the PX4 toolchain or a running SITL
instance. It provides:

  * :func:`action_to_pwm` / :func:`pwm_to_action` — the core unit conversion.
  * :func:`to_mavlink_rc_override` — formats PWM as a MAVLink-style
    ``RC_CHANNELS_OVERRIDE`` payload (mock, dependency-free; uses ``pymavlink``
    automatically if it happens to be installed).
  * :class:`FlightControllerLink` — abstract connection interface.
  * :class:`MockUDPLink` — a UDP socket implementation (PX4's default MAVLink
    transport is UDP). Works in ``dry_run`` mode without any peer listening.
  * :class:`SITLBridge` — ties an (optional) policy module + a link together:
    ``obs -> action -> PWM -> link``.

Standard quad PWM convention used here:
    action  -1.0  ->  1000 us  (motor idle / min)
    action  +1.0  ->  2000 us  (motor full)
    action   0.0  ->  1500 us  (mid)
"""

from __future__ import annotations

import socket
import struct
from abc import ABC, abstractmethod
from typing import Any, Sequence

import numpy as np

PWM_MIN = 1000
PWM_MAX = 2000
PWM_MID = (PWM_MIN + PWM_MAX) // 2


# --------------------------------------------------------------------------- #
# Core conversion
# --------------------------------------------------------------------------- #
def action_to_pwm(
    action: Sequence[float] | np.ndarray,
    pwm_min: int = PWM_MIN,
    pwm_max: int = PWM_MAX,
    clip: bool = True,
) -> np.ndarray:
    """Map normalised actions in [-1, 1] to PWM microseconds in [pwm_min, pwm_max].

    Returns an integer NumPy array (microseconds), one entry per motor.
    """
    a = np.asarray(action, dtype=np.float64).ravel()
    if clip:
        a = np.clip(a, -1.0, 1.0)
    pwm = pwm_min + (a + 1.0) * 0.5 * (pwm_max - pwm_min)
    return np.rint(pwm).astype(int)


def pwm_to_action(
    pwm: Sequence[int] | np.ndarray,
    pwm_min: int = PWM_MIN,
    pwm_max: int = PWM_MAX,
) -> np.ndarray:
    """Inverse of :func:`action_to_pwm` (PWM us -> normalised [-1, 1])."""
    p = np.asarray(pwm, dtype=np.float64).ravel()
    a = 2.0 * (p - pwm_min) / (pwm_max - pwm_min) - 1.0
    return np.clip(a, -1.0, 1.0)


def to_mavlink_rc_override(pwm: Sequence[int] | np.ndarray, n_channels: int = 8) -> dict:
    """Format PWM values as a MAVLink ``RC_CHANNELS_OVERRIDE``-style message.

    Returns a plain dict (chan1_raw .. chanN_raw) so it is inspectable and
    requires no external dependency. Unused channels are set to 0 ("ignore").
    """
    pwm = list(np.asarray(pwm, dtype=int).ravel())
    msg = {"mavpackettype": "RC_CHANNELS_OVERRIDE", "target_system": 1, "target_component": 1}
    for i in range(n_channels):
        msg[f"chan{i + 1}_raw"] = int(pwm[i]) if i < len(pwm) else 0
    return msg


# --------------------------------------------------------------------------- #
# Connection interface
# --------------------------------------------------------------------------- #
class FlightControllerLink(ABC):
    """Abstract link to a flight controller (PX4/ArduPilot)."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def send_pwm(self, pwm: Sequence[int]) -> int:
        """Send one PWM actuator packet. Returns number of bytes sent."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...


class MockUDPLink(FlightControllerLink):
    """UDP link mimicking PX4's default MAVLink-over-UDP transport.

    In ``dry_run`` mode no socket traffic leaves the process (the packet is just
    buffered), so it is safe to instantiate in tests / CI with no peer present.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 14550, dry_run: bool = True) -> None:
        self.host = host
        self.port = int(port)
        self.dry_run = bool(dry_run)
        self._sock: socket.socket | None = None
        self._connected = False
        self.packets_sent = 0
        self.last_packet: bytes | None = None

    def connect(self) -> None:
        if not self.dry_run:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._connected = True

    @staticmethod
    def _pack(pwm: Sequence[int]) -> bytes:
        pwm = list(np.asarray(pwm, dtype=int).ravel())
        # simple wire format: uint16 count + uint16 per channel (little-endian)
        return struct.pack("<H", len(pwm)) + struct.pack(f"<{len(pwm)}H", *pwm)

    def send_pwm(self, pwm: Sequence[int]) -> int:
        if not self._connected:
            raise RuntimeError("MockUDPLink.send_pwm called before connect().")
        packet = self._pack(pwm)
        self.last_packet = packet
        self.packets_sent += 1
        if not self.dry_run and self._sock is not None:
            return self._sock.sendto(packet, (self.host, self.port))
        return len(packet)  # dry-run: report would-be byte count

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


# --------------------------------------------------------------------------- #
# Bridge
# --------------------------------------------------------------------------- #
class SITLBridge:
    """Glue: (optional) RLModule policy -> action -> PWM -> flight-controller link."""

    def __init__(
        self,
        link: FlightControllerLink | None = None,
        module: Any | None = None,
        n_motors: int = 4,
        pwm_min: int = PWM_MIN,
        pwm_max: int = PWM_MAX,
    ) -> None:
        self.link = link if link is not None else MockUDPLink(dry_run=True)
        self.module = module           # an RLlib RLModule, or None for raw mode
        self.n_motors = int(n_motors)
        self.pwm_min = int(pwm_min)
        self.pwm_max = int(pwm_max)

    # -- policy inference (only if a module was supplied) --------------- #
    def policy_action(self, obs: np.ndarray) -> np.ndarray:
        """Deterministic action from the policy (distribution mean)."""
        if self.module is None:
            raise RuntimeError("SITLBridge has no policy module; pass actions directly.")
        import torch  # lazy import: raw-mode users need neither torch nor ray
        from ray.rllib.core.columns import Columns

        batch = {Columns.OBS: torch.from_numpy(np.asarray(obs)[None]).float()}
        with torch.no_grad():
            out = self.module.forward_inference(batch)
        dist_inputs = out[Columns.ACTION_DIST_INPUTS][0].cpu().numpy()
        return np.clip(dist_inputs[: self.n_motors], -1.0, 1.0)

    # -- translation --------------------------------------------------- #
    def translate(self, action: Sequence[float] | np.ndarray) -> np.ndarray:
        """Continuous action [-1, 1] -> PWM microseconds [pwm_min, pwm_max]."""
        return action_to_pwm(action, self.pwm_min, self.pwm_max)

    def mavlink_message(self, action: Sequence[float] | np.ndarray) -> dict:
        return to_mavlink_rc_override(self.translate(action))

    # -- full step ----------------------------------------------------- #
    def send_action(self, action: Sequence[float] | np.ndarray) -> dict:
        """Translate an action to PWM and push it over the link."""
        if not self.link.is_connected:
            self.link.connect()
        pwm = self.translate(action)
        n_bytes = self.link.send_pwm(pwm)
        return {"pwm": pwm, "bytes_sent": n_bytes, "mavlink": to_mavlink_rc_override(pwm)}

    def act_and_send(self, obs: np.ndarray) -> dict:
        """obs -> policy action -> PWM -> link (requires a policy module)."""
        action = self.policy_action(obs)
        result = self.send_action(action)
        result["action"] = action
        return result

    def status(self) -> dict:
        return {
            "has_module": self.module is not None,
            "link": type(self.link).__name__,
            "connected": self.link.is_connected,
            "n_motors": self.n_motors,
            "pwm_band": (self.pwm_min, self.pwm_max),
        }
