"""Gilbert-Elliott fading channel model.

The Gilbert-Elliott (GE) model is a 2-state discrete-time Markov chain widely
used to model bursty packet loss on wireless / tactical radio links:

    GOOD  --p-->  BAD          (p = prob. of degrading per step)
    BAD   --r-->  GOOD         (r = prob. of recovering per step)

Each state carries its own packet-error rate and a fading penalty applied on top
of a deterministic log-distance path-loss model. From that we derive the three
link metrics Phase-1 needs:

    RSSI  [dBm]  = Tx power - path-loss(d) - fading_penalty(state) + thermal noise
    SNR   [dB]   = RSSI - noise_floor
    packet_lost  ~ Bernoulli(error_rate[state])

The class is self-contained and stateful (it owns the current Markov state),
so a MARL runner can instantiate one channel per inter-agent / agent-to-base
link and step them independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np


class ChannelState(IntEnum):
    GOOD = 0
    BAD = 1


@dataclass
class ChannelSample:
    """One step of link telemetry."""

    state: ChannelState
    rssi_dbm: float
    snr_db: float
    packet_lost: bool
    distance_m: float
    transitioned: bool  # did the Markov state change on this step?

    def as_dict(self) -> dict:
        return {
            "state": int(self.state),
            "state_name": self.state.name,
            "rssi_dbm": self.rssi_dbm,
            "snr_db": self.snr_db,
            "packet_lost": bool(self.packet_lost),
            "distance_m": self.distance_m,
            "transitioned": bool(self.transitioned),
        }


class GilbertElliottChannel:
    def __init__(
        self,
        p_good_to_bad: float = 0.05,
        p_bad_to_good: float = 0.40,
        per_good: float = 0.01,
        per_bad: float = 0.55,
        tx_power_dbm: float = 20.0,
        path_loss_exponent: float = 2.2,
        reference_loss_db: float = 40.0,
        reference_distance_m: float = 1.0,
        fading_penalty_bad_db: float = 18.0,
        fading_penalty_good_db: float = 2.0,
        noise_floor_dbm: float = -95.0,
        thermal_noise_std_db: float = 1.5,
        seed: int | None = None,
    ) -> None:
        """
        Parameters
        ----------
        p_good_to_bad, p_bad_to_good : GE Markov transition probabilities.
        per_good, per_bad : packet-error rate in each state.
        tx_power_dbm : transmit power.
        path_loss_exponent : log-distance path-loss exponent ``n``.
        reference_loss_db, reference_distance_m : path loss at the reference
            distance d0 (close-in reference model).
        fading_penalty_{bad,good}_db : extra attenuation while in each state.
        noise_floor_dbm : receiver noise floor used for SNR.
        thermal_noise_std_db : std-dev of per-sample Gaussian sensor noise on
            RSSI (models receiver / thermal jitter -> the "sensor noise" trace).
        """
        self.p_gb = float(p_good_to_bad)
        self.p_bg = float(p_bad_to_good)
        self.per = {ChannelState.GOOD: float(per_good), ChannelState.BAD: float(per_bad)}
        self.tx_power_dbm = float(tx_power_dbm)
        self.n = float(path_loss_exponent)
        self.ref_loss_db = float(reference_loss_db)
        self.d0 = float(reference_distance_m)
        self.fading_penalty = {
            ChannelState.GOOD: float(fading_penalty_good_db),
            ChannelState.BAD: float(fading_penalty_bad_db),
        }
        self.noise_floor_dbm = float(noise_floor_dbm)
        self.thermal_noise_std_db = float(thermal_noise_std_db)
        self._rng = np.random.default_rng(seed)

        self.state: ChannelState = ChannelState.GOOD

    # ------------------------------------------------------------------ #
    def reset(self, state: ChannelState = ChannelState.GOOD) -> None:
        self.state = state

    def _path_loss_db(self, distance_m: float) -> float:
        """Log-distance path loss (clamped at the reference distance)."""
        d = max(distance_m, self.d0)
        return self.ref_loss_db + 10.0 * self.n * np.log10(d / self.d0)

    def _advance_markov(self) -> bool:
        """Advance the GE Markov chain one step. Returns True if it changed."""
        u = self._rng.random()
        prev = self.state
        if self.state == ChannelState.GOOD:
            if u < self.p_gb:
                self.state = ChannelState.BAD
        else:  # BAD
            if u < self.p_bg:
                self.state = ChannelState.GOOD
        return self.state != prev

    def step(self, distance_m: float) -> ChannelSample:
        """Advance the channel one timestep for a link of length ``distance_m``.

        Order: advance the Markov state, then sample RSSI/SNR/loss conditioned
        on the *new* state.
        """
        transitioned = self._advance_markov()

        thermal_noise = self._rng.normal(0.0, self.thermal_noise_std_db)
        rssi = (
            self.tx_power_dbm
            - self._path_loss_db(distance_m)
            - self.fading_penalty[self.state]
            + thermal_noise
        )
        snr = rssi - self.noise_floor_dbm

        packet_lost = bool(self._rng.random() < self.per[self.state])

        return ChannelSample(
            state=self.state,
            rssi_dbm=float(rssi),
            snr_db=float(snr),
            packet_lost=packet_lost,
            distance_m=float(distance_m),
            transitioned=transitioned,
        )

    @property
    def stationary_bad_probability(self) -> float:
        """Long-run fraction of time spent in the BAD state."""
        return self.p_gb / (self.p_gb + self.p_bg)
