"""Plotly visualization for a single simulated episode.

Consumes an :class:`EpisodeLog` populated from a *live* env rollout (no mock
data) and renders a multi-panel dashboard:

    * 3-D trajectory (start, goal, base station, gust markers)
    * Position (x, y, z) vs time
    * RSSI & SNR vs time, with the Gilbert-Elliott state shaded
    * Packet-loss events + wind-force magnitude vs time

The dashboard is saved as a self-contained HTML file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


@dataclass
class EpisodeLog:
    """Time-series buffers filled during a rollout."""

    t: List[float] = field(default_factory=list)
    pos: List[np.ndarray] = field(default_factory=list)
    vel: List[np.ndarray] = field(default_factory=list)
    rssi: List[float] = field(default_factory=list)
    snr: List[float] = field(default_factory=list)
    comms_state: List[int] = field(default_factory=list)
    packet_lost: List[bool] = field(default_factory=list)
    transitioned: List[bool] = field(default_factory=list)
    wind_force: List[np.ndarray] = field(default_factory=list)
    reward: List[float] = field(default_factory=list)

    goal: np.ndarray | None = None
    base_station: np.ndarray | None = None
    start: np.ndarray | None = None
    gust_steps: List[int] = field(default_factory=list)

    def record(self, *, t, pos, vel, sample, wind_force, reward):
        self.t.append(float(t))
        self.pos.append(np.asarray(pos, dtype=np.float64).copy())
        self.vel.append(np.asarray(vel, dtype=np.float64).copy())
        self.rssi.append(float(sample["rssi_dbm"]))
        self.snr.append(float(sample["snr_db"]))
        self.comms_state.append(int(sample["state"]))
        self.packet_lost.append(bool(sample["packet_lost"]))
        self.transitioned.append(bool(sample["transitioned"]))
        self.wind_force.append(np.asarray(wind_force, dtype=np.float64).copy())
        self.reward.append(float(reward))

    # convenience views ------------------------------------------------- #
    @property
    def pos_arr(self) -> np.ndarray:
        return np.asarray(self.pos) if self.pos else np.zeros((0, 3))

    @property
    def wind_arr(self) -> np.ndarray:
        return np.asarray(self.wind_force) if self.wind_force else np.zeros((0, 3))


def _bad_state_spans(t: List[float], comms_state: List[int]) -> List[tuple[float, float]]:
    """Return [start, end] time spans where the GE chain is in the BAD state."""
    spans = []
    in_bad = False
    start = None
    for i, s in enumerate(comms_state):
        if s == 1 and not in_bad:
            in_bad = True
            start = t[i]
        elif s == 0 and in_bad:
            in_bad = False
            spans.append((start, t[i]))
    if in_bad:
        spans.append((start, t[-1]))
    return spans


def build_dashboard(log: EpisodeLog, title: str = "Dexia Phase 1 — Episode Telemetry") -> go.Figure:
    pos = log.pos_arr
    wind = log.wind_arr
    wind_mag = np.linalg.norm(wind, axis=1) if len(wind) else np.zeros(0)

    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[
            [{"type": "scene", "rowspan": 2}, {"type": "xy"}],
            [None, {"type": "xy"}],
        ],
        column_widths=[0.5, 0.5],
        row_heights=[0.5, 0.5],
        subplot_titles=(
            "3-DOF Trajectory",
            "Position & Comms Link (RSSI / SNR)",
            "Packet Loss & Wind Disturbance",
        ),
        horizontal_spacing=0.09,
        vertical_spacing=0.12,
    )

    # ---- 3D trajectory (col 1) --------------------------------------- #
    if len(pos):
        fig.add_trace(
            go.Scatter3d(
                x=pos[:, 0], y=pos[:, 1], z=pos[:, 2],
                mode="lines",
                line=dict(color="royalblue", width=4),
                name="trajectory",
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter3d(
                x=[pos[0, 0]], y=[pos[0, 1]], z=[pos[0, 2]],
                mode="markers", marker=dict(size=6, color="green"), name="start",
            ),
            row=1, col=1,
        )
        # gust markers along the trajectory
        gust_idx = [i for i, tt in enumerate(log.t) if int(tt) in log.gust_steps]
        gi = [i for i in range(len(pos)) if i in gust_idx] if gust_idx else []
        if gi:
            fig.add_trace(
                go.Scatter3d(
                    x=pos[gi, 0], y=pos[gi, 1], z=pos[gi, 2],
                    mode="markers",
                    marker=dict(size=4, color="orange", symbol="diamond"),
                    name="wind gust",
                ),
                row=1, col=1,
            )
    if log.goal is not None:
        fig.add_trace(
            go.Scatter3d(
                x=[log.goal[0]], y=[log.goal[1]], z=[log.goal[2]],
                mode="markers", marker=dict(size=8, color="red", symbol="x"), name="goal",
            ),
            row=1, col=1,
        )
    if log.base_station is not None:
        fig.add_trace(
            go.Scatter3d(
                x=[log.base_station[0]], y=[log.base_station[1]], z=[log.base_station[2]],
                mode="markers", marker=dict(size=8, color="black", symbol="square"),
                name="base station",
            ),
            row=1, col=1,
        )

    # ---- Position + comms (row1 col2) -------------------------------- #
    # Shade BAD comms-state spans FIRST (drawn behind the data lines). We use
    # filled Scatter rectangles rather than ``add_vrect`` because the latter's
    # axis-spanning helper does not cope with the rowspan=2 3D scene in col 1.
    panel1_vals = []
    if len(pos):
        panel1_vals.append(pos.ravel())
    panel1_vals.append(np.asarray(log.rssi))
    panel1_vals.append(np.asarray(log.snr))
    allv = np.concatenate([v for v in panel1_vals if len(v)]) if panel1_vals else np.array([0.0, 1.0])
    ylo, yhi = float(np.min(allv)), float(np.max(allv))
    pad = 0.05 * (yhi - ylo + 1e-9)
    ylo, yhi = ylo - pad, yhi + pad

    _first_band = True
    for (s0, s1) in _bad_state_spans(log.t, log.comms_state):
        fig.add_trace(
            go.Scatter(
                x=[s0, s1, s1, s0, s0],
                y=[ylo, ylo, yhi, yhi, ylo],
                fill="toself",
                fillcolor="rgba(214,39,40,0.10)",
                line=dict(width=0),
                mode="lines",
                name="GE BAD state",
                legendgroup="badstate",
                showlegend=_first_band,
                hoverinfo="skip",
            ),
            row=1, col=2,
        )
        _first_band = False

    if len(pos):
        for k, axis in enumerate(["x", "y", "z"]):
            fig.add_trace(
                go.Scatter(x=log.t, y=pos[:, k], mode="lines", name=f"pos {axis} [m]",
                           legendgroup="pos"),
                row=1, col=2,
            )
    fig.add_trace(
        go.Scatter(x=log.t, y=log.rssi, mode="lines", name="RSSI [dBm]",
                   line=dict(color="purple"), legendgroup="link"),
        row=1, col=2,
    )
    fig.add_trace(
        go.Scatter(x=log.t, y=log.snr, mode="lines", name="SNR [dB]",
                   line=dict(color="teal", dash="dot"), legendgroup="link"),
        row=1, col=2,
    )

    # ---- Packet loss + wind (row2 col2) ------------------------------ #
    wind_hi = float(np.max(wind_mag)) if len(wind_mag) else 1.0
    wind_hi = max(wind_hi, 1.0)

    # Triggered-gust onset lines (drawn as Scatter, behind the data).
    _first_gust = True
    for gstep in log.gust_steps:
        fig.add_trace(
            go.Scatter(
                x=[gstep, gstep], y=[0, wind_hi * 1.05],
                mode="lines",
                line=dict(color="orange", dash="dash", width=1.5),
                name="gust triggered",
                legendgroup="gust",
                showlegend=_first_gust,
                hoverinfo="skip",
            ),
            row=2, col=2,
        )
        _first_gust = False

    if len(wind_mag):
        fig.add_trace(
            go.Scatter(x=log.t, y=wind_mag, mode="lines", name="wind |F| [N]",
                       line=dict(color="darkorange")),
            row=2, col=2,
        )
    lost_t = [log.t[i] for i, l in enumerate(log.packet_lost) if l]
    lost_y = [wind_hi * 1.02 for _ in lost_t]
    fig.add_trace(
        go.Scatter(x=lost_t, y=lost_y, mode="markers",
                   marker=dict(color="red", symbol="x", size=7),
                   name="packet lost"),
        row=2, col=2,
    )

    fig.update_xaxes(title_text="step", row=1, col=2)
    fig.update_xaxes(title_text="step", row=2, col=2)
    fig.update_yaxes(title_text="m / dB(m)", row=1, col=2)
    fig.update_yaxes(title_text="loss / N", row=2, col=2)
    fig.update_scenes(
        xaxis_title="x [m]", yaxis_title="y [m]", zaxis_title="z [m]",
        aspectmode="data",
    )
    fig.update_layout(
        title=title,
        height=820,
        legend=dict(orientation="h", yanchor="bottom", y=-0.08),
        margin=dict(l=20, r=20, t=70, b=40),
    )
    return fig


def save_dashboard(log: EpisodeLog, path: str, title: str | None = None) -> str:
    fig = build_dashboard(log) if title is None else build_dashboard(log, title)
    fig.write_html(path, include_plotlyjs="cdn", full_html=True)
    return path
