"""Decision policy — collect-or-act over the AssetMatch matrix (C3).

The deterministic tactical logic the design calls for: confront each track in
threat order; if it is too uncertain to engage, *collect* (task an ISR asset to
confirm it) — information gathering is a first-class action, not a passive input;
once confirmed, *act* with the best feasible asset, suppressing air-defense
emitters before committing strikers. One asset is tasked at most once per cycle.

Pure functions over Track + Option objects → no LLM dependency (DESIGN_AIP.md 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .assetmatch import EMITTERISH

_KIND = {"jam": "suppress", "request_fires": "fires",
         "engage": "strike", "task_isr": "collect"}


@dataclass
class Decision:
    track_id: str
    asset_id: str
    cmd: str
    target: list
    kind: str                             # collect | suppress | fires | strike
    rationale: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {"track_id": self.track_id, "asset": self.asset_id, "cmd": self.cmd,
                "kind": self.kind, "target": [round(self.target[0]), round(self.target[1])],
                "why": self.rationale, "score": self.score}


def _priority(track):
    # emitters / air defence first (they threaten our air assets), then by
    # descending confidence so we finish what we can confirm.
    return (0 if track.category in EMITTERISH else 1, -float(track.confidence))


def _rationale(track, opt, intent: str) -> str:
    if opt.cmd == "jam":
        return (f"{track.track_id}({track.category}) 레이더 활성 — 항공자산 투입 전 "
                f"{opt.asset_id}로 전자전 제압")
    if opt.cmd == "request_fires":
        return (f"{track.track_id}({track.category}) conf {track.confidence} 확정 — "
                f"의도 '{intent}'에 따라 {opt.asset_id} 간접화력 (이동표적 예측사격)")
    if opt.cmd == "engage":
        return f"{track.track_id} 확정 — {opt.asset_id} 배회폭탄 타격"
    return f"{track.track_id} conf {track.confidence} 불확실 — {opt.asset_id} ISR 재배치로 확인"


def decide(tracks, matrix, intent: str, *, confirm_threshold: float = 0.6,
           committed=None) -> list:
    """Return the Decisions for this cycle (each on a distinct asset).

    ``committed`` — track ids with fires/strike already inbound; we don't
    re-allocate weapons to them, so the agent spreads effects across the force
    instead of hammering one target (suppression re-application still allowed)."""
    committed = committed or set()
    used: set = set()
    decisions: list = []
    for tr in sorted(tracks, key=_priority):
        opts = [o for o in matrix.get(tr.track_id, [])
                if o.feasible and o.asset_id not in used]
        if not opts:
            continue
        if tr.confidence < confirm_threshold:
            # collect first — only an ISR option is appropriate for a vague track
            isr = next((o for o in opts if o.cmd == "task_isr"), None)
            if isr is None:
                continue
            used.add(isr.asset_id)
            decisions.append(Decision(tr.track_id, isr.asset_id, "task_isr", isr.target,
                                      "collect", _rationale(tr, isr, intent), isr.score))
            continue
        # confirmed — act with the best feasible weapon/effect (skip ISR options)
        act = next((o for o in opts if o.cmd != "task_isr"), None)
        if act is None:
            continue
        # don't pile fresh fires/strike onto a target already under inbound fire
        if act.cmd in ("request_fires", "engage") and tr.track_id in committed:
            continue
        used.add(act.asset_id)
        decisions.append(Decision(tr.track_id, act.asset_id, act.cmd, act.target,
                                  _KIND.get(act.cmd, "act"), _rationale(tr, act, intent),
                                  act.score))
    return decisions
