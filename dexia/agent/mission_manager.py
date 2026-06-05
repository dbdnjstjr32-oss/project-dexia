"""Mission Manager (Live OODA Loop).

Orchestrates the live simulation, LLM-first reasoning, and the approval queue.
Replaces the batch `MissionRunner`.
"""

import functools
import json
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _locked(fn):
    """Run a MissionManager method under its state lock. For fast, LLM-free methods
    callable from the command server's event loop (broadcast/approvals) so they
    can't race the worker-thread ``run_cycle``. RLock => reentrant nesting is safe."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper

from dexia.fusion import EffectResolver, FusionEngine, WorldState, build_feeds
from dexia.agent.assetmatch import match_assets
from dexia.ontology.action_bus import ActionBus
from dexia.ontology.registry import InMemoryRegistry
from dexia.ontology.actions import ACTION_REGISTRY, TOOL_TO_ACTION
from dexia.api.llm_gateway import get_gateway
from dexia.scenario.catalog import get_catalog

# Canonical actions the AIP staff may put in a COA, with a one-line brief and the
# capability an asset must have to perform it. Surfaced into the LLM prompt so the
# model fills COA.action with a REAL action name (not free text) and picks an asset
# that can actually do it. This is the LLM<->ActionBus contract.
COA_ACTIONS = {
    "request_fires": ("indirect_fire",
                      "call indirect artillery/MLRS fire onto a target (beyond line-of-sight)"),
    "engage":        ("loiter_strike",
                      "lethal loitering-munition / direct strike on a target"),
    "jam":           ("jam",
                      "suppress an enemy emitter (SAM/EW radar) to open a window"),
    "task_isr":      ("__sensor__",
                      "reposition a recon/ISR sensor over an area to confirm a low-confidence track"),
}


@dataclass
class COA:
    id: str
    action: str
    target: str
    asset: str
    description: str
    confidence: float
    expected_success: float
    status: str = "pending"   # pending | approved | rejected | executed
    # Phase B — kill/loss balance for ranked options (안1/안2/안3)
    p_kill: float = 0.0       # estimated probability the target is neutralized
    est_loss: float = 0.0     # estimated friendly loss/risk [0..1]
    rank: int = 0             # 1 = AIP's recommended option

class MissionManager:
    def __init__(self, world: WorldState, red_commander, tick_rate_hz=1):
        self.world = world
        self.catalog = get_catalog()
        # Feed setup: honour the scenario's declared feeds when present, otherwise
        # fall back to the standard recon triad.
        feed_ids = getattr(getattr(world, "scenario", None), "feeds", None) \
            or ["uav_eo", "sigint", "gsr"]
        self.feeds = build_feeds(feed_ids, self.catalog)
        self.fusion = FusionEngine()
        self.resolver = EffectResolver(self.world, self.catalog)
        self.bus = ActionBus(InMemoryRegistry())
        self.red_commander = red_commander

        self.tick_rate_hz = tick_rate_hz
        self.current_tick = 0

        self.llm = get_gateway()
        self.aip_feed: List[Dict] = []
        self.approval_queue: List[COA] = []
        self._coa_seq = 0
        self._engaged: Dict[str, int] = {}   # track_id -> tick we struck it (for BDA)

        # Guards live state against the command server's worker thread (run_cycle)
        # vs the event loop (broadcast / approvals). Held only for fast state
        # mutations — NEVER across the blocking LLM call — so broadcasts never
        # freeze while the AIP is reasoning (Phase F).
        self._lock = threading.RLock()

        # State machine
        self.paused = False

    # ------------------------------------------------------------------ #
    # OBSERVE
    # ------------------------------------------------------------------ #
    def _observe(self) -> List[str]:
        """Step the world and gather fused intelligence (Fog of War).

        Returns the list of track_ids that were *first seen this tick* so the
        caller can trigger reasoning the moment something new is detected.
        """
        # 1. Step world
        self.world.step(1.0 / self.tick_rate_hz)
        self.current_tick += 1

        # 2. Red commander step
        self.red_commander.step(self.world, self.current_tick)

        # 3. Resolve delayed physics/effects (rounds landing, etc.) and surface
        #    them on the AIP feed — this is the BDA the commander sees.
        for ev in self.resolver.step(self.current_tick):
            self._log_effect(ev)

        # 4. Observe feeds
        dets = []
        for f in self.feeds:
            ds = f.observe(self.world, self.current_tick, None)
            if ds:
                dets += ds

        # 5. Fuse — diff track ids to detect brand-new tracks
        before = {t.track_id for t in self.fusion.active_tracks()}
        self.fusion.update(dets, self.current_tick)
        after = {t.track_id for t in self.fusion.active_tracks()}
        return sorted(after - before)

    def _log_effect(self, ev) -> None:
        """Turn an EffectResolver event into an AIP feed entry (BDA / execution)."""
        if ev is None:
            return
        if ev.status == "impact":
            kills = ", ".join(ev.killed) if ev.killed else "no targets in lethal radius"
            self.aip_feed.append({
                "type": "BDA",
                "message": f"{ev.action} impact: {kills}. {ev.detail}",
                "killed": ev.killed,
                "tick": ev.tick,
            })
        elif ev.status == "launched":
            self.aip_feed.append({
                "type": "EXECUTION",
                "message": f"{ev.asset_id}: {ev.detail}",
                "tick": ev.tick,
            })
        elif ev.status in ("suppressed", "tasked"):
            self.aip_feed.append({
                "type": "EXECUTION",
                "message": f"{ev.asset_id}: {ev.detail}",
                "tick": ev.tick,
            })
        elif ev.status == "rejected":
            self.aip_feed.append({
                "type": "ERROR",
                "message": f"{ev.action} rejected: {ev.detail}",
                "tick": ev.tick,
            })

    # ------------------------------------------------------------------ #
    # ORIENT + DECIDE
    # ------------------------------------------------------------------ #
    def _asset_capabilities(self, e) -> List[str]:
        """Which COA actions a blue entity can actually perform (catalog-driven)."""
        spec = self.catalog.get(e.cls)
        if spec is None:
            return []
        caps = []
        for action, (need, _desc) in COA_ACTIONS.items():
            if need == "__sensor__":
                if spec.sensors:
                    caps.append(action)
            elif spec.effect(need) is not None:
                caps.append(action)
        return caps

    def _orient_and_decide(self, user_modification: str = None):
        """Call LLM to analyze tracks and propose COAs (with a constrained action vocabulary)."""
        # Snapshot live state under the lock (fast), then build+call the LLM lockless.
        with self._lock:
            tracks = self.fusion.snapshot()
            blue_assets = []
            available_actions = set()
            for e in self.world.blue:
                caps = self._asset_capabilities(e)
                available_actions.update(caps)
                blue_assets.append({
                    "id": e.entity_id, "cls": e.cls, "pos": [round(c, 1) for c in e.position[:2]],
                    "ammo": (None if e.ammo == float("inf") else e.ammo),
                    "can_perform": caps,
                })

        # Build the action contract the model MUST obey.
        action_lines = [f'  - "{a}": {COA_ACTIONS[a][1]}'
                        for a in COA_ACTIONS if a in available_actions]
        action_brief = "\n".join(action_lines) or "  (no offensive/ISR assets available)"

        prompt = (
            f"Battlefield assessment.\n"
            f"TRACKS (known/suspected enemy, fog-of-war): {json.dumps(tracks)}\n"
            f"BLUE ASSETS (yours): {json.dumps(blue_assets)}\n\n"
        )
        if user_modification:
            prompt += f"COMMANDER MODIFICATION: {user_modification}\nAdjust your plan accordingly.\n\n"

        prompt += (
            "Decide the next move. If a target is confirmed enough to act, propose one or more COAs.\n"
            "Each COA's \"action\" MUST be EXACTLY one of these canonical action names:\n"
            f"{action_brief}\n\n"
            "Rules:\n"
            "  - \"action\" is the canonical name above ONLY (never free text / never a sentence).\n"
            "  - \"asset\" MUST be an 'id' from BLUE ASSETS whose 'can_perform' includes that action.\n"
            "  - \"target\" MUST be a 'track_id' from TRACKS (for fires/engage/jam), "
            "or omit/leave blank and rely on ISR to build a track first.\n"
            "  - Put human-readable reasoning in \"description\", NOT in \"action\".\n"
            "  - If nothing is confirmed yet, return coas: [] and recommend ISR in the feed.\n\n"
            "Return JSON ONLY (no markdown): {\"feed\": [{\"type\": \"ISR|ANALYSIS|RECOMMENDATION\", "
            "\"message\": str}], \"coas\": [{\"action\": str, \"target\": str, \"asset\": str, "
            "\"description\": str, \"confidence\": float, \"expected_success\": float}]}"
        )

        res = self.llm.chat([
            {"role": "system", "content": "You are a tactical AIP staff officer. Output valid JSON only. "
                                          "Use only the canonical action names you are given."},
            {"role": "user", "content": prompt}
        ], use_case="tactical")

        if not res.get("ok"):
            with self._lock:
                self.aip_feed.append({"type": "ERROR", "message": "AIP unavailable (LLM offline)"})
            return

        try:
            content = res["response"]["message"]["content"]
            if isinstance(content, dict):
                data = content
            else:
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                elif content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                data = json.loads(content)
        except Exception as e:
            with self._lock:
                self.aip_feed.append({"type": "ERROR", "message": f"LLM parsing failed: {e}"})
            return

        # Apply results under the lock (fast): feed + validated COAs.
        with self._lock:
            for f in data.get("feed", []):
                self.aip_feed.append(f)
            new_coas = [coa for coa in (self._validate_coa(c) for c in data.get("coas", []))
                        if coa is not None]
            if new_coas:
                self.approval_queue.extend(new_coas)
                self.paused = True  # wait for human approval

    def _validate_coa(self, c: dict) -> Optional[COA]:
        """Normalize + validate one LLM-proposed COA against the real action/asset/track
        ontology. Rejects (and explains on the feed) anything that could not execute,
        so a garbled proposal never reaches the approval queue as a dead action."""
        raw_action = str(c.get("action", "")).strip()
        # accept either a canonical name or an Ollama tool_name; reject free text.
        action = raw_action if raw_action in ACTION_REGISTRY else TOOL_TO_ACTION.get(raw_action)
        if action not in COA_ACTIONS:
            self.aip_feed.append({
                "type": "ANALYSIS",
                "message": f"Discarded proposal with invalid action '{raw_action}'.",
            })
            return None

        # resolve asset id -> a real, living blue entity with the right capability
        asset = self._resolve_asset(c.get("asset"), action)
        if asset is None:
            self.aip_feed.append({
                "type": "ANALYSIS",
                "message": f"Discarded '{action}': no available asset can perform it "
                           f"(proposed '{c.get('asset')}').",
            })
            return None

        # lethal/effect actions require a known track; ISR may be area-only
        target = str(c.get("target") or "").strip()
        if action in ("request_fires", "engage", "jam"):
            track_ids = {t.track_id for t in self.fusion.active_tracks()}
            if target not in track_ids:
                self.aip_feed.append({
                    "type": "ANALYSIS",
                    "message": f"Discarded '{action}': target '{target}' is not a known track.",
                })
                return None

        self._coa_seq += 1
        return COA(
            id=f"COA-{self._coa_seq:03d}",
            action=action,
            target=target,
            asset=asset.entity_id,
            description=str(c.get("description", "")),
            confidence=float(c.get("confidence", 0.0) or 0.0),
            expected_success=float(c.get("expected_success", 0.0) or 0.0),
        )

    def _resolve_asset(self, asset_ref, action: str):
        """Map an LLM asset reference (entity id OR class name) to a living blue
        entity that can perform ``action``. Falls back to any capable asset."""
        ref = str(asset_ref or "").strip()
        base = re.sub(r"_\d+$", "", ref)
        # 1) exact entity id, if capable
        e = self.world.get(ref)
        if e is not None and e.alive and action in self._asset_capabilities(e):
            return e
        # 2) class-name match (e.g. "m777_howitzer" -> first living instance)
        for e in self.world.blue:
            if (e.cls == ref or e.cls == base) and action in self._asset_capabilities(e):
                return e
        # 3) any living asset that can do it
        for e in self.world.blue:
            if action in self._asset_capabilities(e):
                return e
        return None

    # ------------------------------------------------------------------ #
    # LOOP
    # ------------------------------------------------------------------ #
    def run_cycle(self):
        """Run one tick of the OODA loop if not paused."""
        with self._lock:
            if self.paused:
                return
            new_tracks = self._observe()             # fast: world step + fuse
            due = bool(new_tracks) or self.current_tick % 10 == 0
        # The LLM call (slow) runs OUTSIDE the lock so broadcasts/approvals don't
        # block on it. _orient_and_decide re-takes the lock only to apply results.
        if due:
            self._orient_and_decide()

    # ------------------------------------------------------------------ #
    # ACT
    # ------------------------------------------------------------------ #
    # Only lead a target whose estimated speed is clearly above the velocity-noise
    # floor; otherwise extrapolating a jittery fused position over a 40 s time-of-
    # flight throws the aimpoint far off a stationary unit (lead from noise).
    LEAD_SPEED_FLOOR_MPS = 6.0

    def _aimpoint_for(self, coa: COA):
        """Resolve a COA target track_id to a fused aimpoint, leading a genuine
        mover by the firing asset's time-of-flight."""
        for t in self.fusion.active_tracks():
            if t.track_id != coa.target:
                continue
            asset = self.world.get(coa.asset)
            spec = self.catalog.get(asset.cls) if asset else None
            eff = (spec.effect("indirect_fire") or spec.effect("loiter_strike")) if spec else None
            if eff is not None and eff.tof_s > 0 and hasattr(t, "predicted"):
                v = t.velocity
                speed = (v[0] ** 2 + v[1] ** 2) ** 0.5
                if speed >= self.LEAD_SPEED_FLOOR_MPS:
                    return list(t.predicted(eff.tof_s))
            return list(t.position)
        return None

    @_locked
    def approve_coa(self, coa_id: str):
        """Human approves a COA. Execute it through the governed ActionBus funnel."""
        chosen = next((c for c in self.approval_queue if c.id == coa_id), None)
        for coa in self.approval_queue:
            if coa.id != coa_id:
                continue
            coa.status = "approved"

            payload = {"asset_id": coa.asset, "target_id": coa.target}
            aim = self._aimpoint_for(coa)
            if aim is not None:
                payload["target"] = aim
                payload["x"], payload["y"] = aim[0], aim[1]

            res = self.bus.submit(coa.action, agent_id=coa.asset,
                                  payload=payload, clearance="commander")
            if res["status"] == "accepted" and res.get("command"):
                ev = self.resolver.submit(res["command"], self.current_tick)
                self._log_effect(ev)
                coa.status = "executed"
                if coa.action in ("request_fires", "engage") and coa.target:
                    self._engaged[coa.target] = self.current_tick   # mark for BDA re-recon
                self.aip_feed.append({
                    "type": "EXECUTION",
                    "message": f"Approved & executed {coa.id}: {coa.action} on {coa.target} "
                               f"by {coa.asset}",
                })
            else:
                self.aip_feed.append({
                    "type": "ERROR",
                    "message": f"Failed to execute {coa.id}: {res.get('reason')}",
                })

        # Ranked options (안1/2/3) are mutually-exclusive alternatives to ONE
        # decision — approving one retires the rest. Non-ranked COAs are removed
        # individually. Unpause once nothing awaits approval.
        if chosen is not None and chosen.rank:
            dropped = [c.id for c in self.approval_queue if c.id != coa_id and c.rank]
            self.approval_queue = [c for c in self.approval_queue if not c.rank]
            if dropped:
                self.aip_feed.append({"type": "COMMANDER",
                                      "message": f"대안 자동 정리: {', '.join(dropped)} (안 선택됨)"})
        else:
            self.approval_queue = [c for c in self.approval_queue if c.id != coa_id]
        if not self.approval_queue:
            self.paused = False

    # ------------------------------------------------------------------ #
    # BDA (Phase C) — sensor-confirmed via re-recon, NOT the ground-truth flag
    # ------------------------------------------------------------------ #
    def bda_status(self, track_id: str, stale_after: int = 8) -> str:
        """Sensor-based battle-damage verdict for a struck track. 'destroyed' if the
        track is gone/stale or hasn't been re-seen despite recon coverage; 'survived'
        if a sensor is still actively detecting it. Reads only fused intelligence —
        never the world's ground-truth ``alive`` flag."""
        tr = next((t for t in self.fusion.active_tracks() if t.track_id == track_id), None)
        if tr is None or tr.status == "stale":
            return "destroyed"
        if self.current_tick - tr.last_seen_tick > stale_after:
            return "destroyed"
        return "survived"

    @_locked
    def assess_bda(self) -> Dict[str, str]:
        """Run a BDA verdict for every track we struck (call after a re-recon sweep
        has had time to re-cover the area). Returns {track_id: 'destroyed'|'survived'}."""
        out: Dict[str, str] = {}
        for tid in list(self._engaged):
            st = self.bda_status(tid)
            out[tid] = st
            self.aip_feed.append({
                "type": "BDA",
                "message": f"재정찰 판정(sensor-confirmed): {tid} → "
                           f"{'격멸(destroyed)' if st == 'destroyed' else '생존(survived)'}",
            })
            if st == "destroyed":
                self._engaged.pop(tid, None)   # closed out; survivors stay open for re-strike
        return out

    # ------------------------------------------------------------------ #
    # STRIKE OPTIONS (Phase B) — ranked 안1/안2/안3 balancing kill vs loss
    # ------------------------------------------------------------------ #
    # weight on friendly loss when ranking (kill is weight 1.0)
    LOSS_WEIGHT = 0.6

    def _p_kill(self, track, opt) -> float:
        """Estimated probability the target is neutralized — from the catalog effect
        (lethal radius vs the track's position uncertainty) and track confidence."""
        asset = self.world.get(opt.asset_id)
        spec = self.catalog.get(asset.cls) if asset else None
        if spec is None:
            return 0.0
        eff = spec.effect("indirect_fire") if opt.cmd == "request_fires" else spec.effect("loiter_strike")
        if eff is None:
            return 0.0
        lethal = eff.lethal_r + (120.0 if opt.cmd == "engage" else 0.0)  # terminal seeker capture
        cover = lethal / (lethal + max(track.uncertainty_r, 1.0))
        return round(min(0.98, float(track.confidence) * (0.5 + 0.5 * cover)), 3)

    def _est_loss(self, track, opt, tracks) -> float:
        """Estimated friendly loss/risk for this option. Indirect fires from depth is
        low-risk; a loitering munition that must fly in is exposed to known air
        defences — so the same kill can carry very different cost."""
        base = {"request_fires": 0.05, "engage": 0.25, "jam": 0.10}.get(opt.cmd, 0.10)
        air_defence = any(t.category in ("air_defense", "emitter", "ew") for t in tracks)
        if air_defence:
            base += 0.35 if opt.cmd == "engage" else 0.05  # fly-in exposed; arty barely
        return round(min(0.95, base), 3)

    @_locked
    def generate_coa_options(self, max_options: int = 3, min_conf: float = 0.5):
        """Compute ranked strike options (안1/안2/안3) over confirmed targets,
        balancing estimated enemy kill against estimated friendly loss. Populates the
        approval queue and pauses for the commander to select/modify."""
        tracks = [t for t in self.fusion.active_tracks() if t.confidence >= min_conf]
        if not tracks:
            self.aip_feed.append({"type": "ANALYSIS",
                                  "message": "No confirmed targets yet — recon required before a strike plan."})
            return []

        matrix = match_assets(tracks, list(self.world.blue), self.catalog)
        cand = []
        for tr in tracks:
            for o in matrix.get(tr.track_id, []):
                if o.cmd not in ("request_fires", "engage") or not o.feasible:
                    continue
                pk = self._p_kill(tr, o)
                loss = self._est_loss(tr, o, tracks)
                if pk <= 0.0:
                    continue
                cand.append((round(pk - self.LOSS_WEIGHT * loss, 3), pk, loss, tr, o))

        # best per (asset,target), then global best-first
        best = {}
        for c in cand:
            key = (c[4].asset_id, c[3].track_id)
            if key not in best or c[0] > best[key][0]:
                best[key] = c
        ranked = sorted(best.values(), key=lambda c: c[0], reverse=True)[:max_options]
        if not ranked:
            self.aip_feed.append({"type": "ANALYSIS",
                                  "message": "Targets confirmed but no feasible strike asset in range."})
            return []

        self.approval_queue.clear()
        coas = []
        summary = ["AIP 타격 방안 (격파확률 vs 아군손실):"]
        for i, (score, pk, loss, tr, o) in enumerate(ranked, start=1):
            self._coa_seq += 1
            coa = COA(
                id=f"COA-{i}", action=o.cmd, target=tr.track_id, asset=o.asset_id,
                description=f"안{i}: {o.asset_id} → {o.cmd} on {tr.track_id} ({tr.category}) | {o.reason}",
                confidence=float(tr.confidence), expected_success=pk,
                p_kill=pk, est_loss=loss, rank=i,
            )
            coas.append(coa)
            summary.append(f"  안{i}: {o.asset_id} {o.cmd} → {tr.track_id} "
                           f"| Pkill {pk:.0%}  아군손실 {loss:.0%}  (score {score:.2f})")
        self.approval_queue.extend(coas)
        self.paused = True
        self.aip_feed.append({"type": "RECOMMENDATION", "message": "\n".join(summary)})
        return coas

    # ------------------------------------------------------------------ #
    # RECON PLANNING (Phase A) — "AIP reads the terrain and plans a route"
    # ------------------------------------------------------------------ #
    def _resolve_recon_asset(self, asset_ref: str = None):
        """Pick an ISR platform: a living air sensor first, else any living sensor."""
        if asset_ref:
            e = self.world.get(asset_ref)
            if e is not None and e.alive:
                spec = self.catalog.get(e.cls)
                if spec and spec.sensors:
                    return e
        for want_air in (True, False):
            for e in self.world.blue:
                spec = self.catalog.get(e.cls)
                if spec and spec.sensors and (spec.domain == "air") == want_air:
                    return e
        return None

    def _default_recon_area(self):
        """The unconfirmed enemy region: a box forward (north) of the blue line.
        Honest fog-of-war — derived from where our own forces are, not from the
        hidden enemy truth."""
        blues = [e for e in self.world.blue]
        if blues:
            cx = sum(e.position[0] for e in blues) / len(blues)
            fwd = max(e.position[1] for e in blues)
        else:
            cx, fwd = 0.0, -2000.0
        return (cx - 3000.0, fwd + 2000.0, cx + 3000.0, fwd + 8000.0)

    def plan_recon_route(self, area=None, sweeps: int = 3):
        """Plan a terrain-aware lawnmower ISR sweep over ``area`` ending in a loiter
        orbit at its centre. Reads the terrain to set a safe cruise altitude above
        the highest ground in the box (this is the AIP 'reading the terrain')."""
        if area is None:
            area = self._default_recon_area()
        x0, y0, x1, y1 = (float(v) for v in area)
        sweeps = max(2, int(sweeps))
        ys = [y0 + (y1 - y0) * i / (sweeps - 1) for i in range(sweeps)]
        wps: List[list] = []
        for i, yy in enumerate(ys):
            wps += ([[x0, yy], [x1, yy]] if i % 2 == 0 else [[x1, yy], [x0, yy]])
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        terrain = getattr(self.world, "terrain", None)
        # safe altitude = highest ground sampled across the box + standoff
        ground = 0.0
        if terrain is not None:
            samples = [terrain.height(x, y)
                       for x in (x0, cx, x1) for y in (y0, cy, y1)]
            ground = max(samples)
        orbit = {"center": [cx, cy], "r": 800.0, "alt": ground + 1500.0}
        return wps, orbit

    @_locked
    def command_recon(self, area=None, asset_ref: str = None):
        """Commander/AIP-directed ISR: plan a route from terrain and fly it through
        the governed funnel. Returns the tasked asset id (or None)."""
        asset = self._resolve_recon_asset(asset_ref)
        if asset is None:
            self.aip_feed.append({"type": "ERROR", "message": "No ISR asset available for recon."})
            return None
        wps, orbit = self.plan_recon_route(area)
        self.aip_feed.append({
            "type": "ISR",
            "message": f"AIP plan: {asset.entity_id} ISR sweep — {len(wps)} waypoints over the "
                       f"unconfirmed area, then orbit @ {int(orbit['alt'])} m AGL.",
        })
        payload = {"asset_id": asset.entity_id, "route": wps, "orbit": orbit}
        res = self.bus.submit("recon_route", agent_id=asset.entity_id,
                              payload=payload, clearance="commander")
        if res["status"] == "accepted" and res.get("command"):
            self._log_effect(self.resolver.submit(res["command"], self.current_tick))
        else:
            self.aip_feed.append({"type": "ERROR",
                                  "message": f"Recon tasking rejected: {res.get('reason')}"})
        return asset.entity_id

    @_locked
    def command_fires(self, track_id: str, asset_ref: str = None):
        """Commander-directed fire mission (human authority). Builds a COA and runs
        it through the SAME governed ActionBus funnel as an AIP-proposed strike —
        the human-in-the-loop path the AIP plan calls for, not a back door."""
        asset = self._resolve_asset(asset_ref, "request_fires")
        if asset is None:
            self.aip_feed.append({"type": "ERROR",
                                  "message": "Commander fire order: no fires asset available."})
            return None
        if track_id not in {t.track_id for t in self.fusion.active_tracks()}:
            self.aip_feed.append({"type": "ERROR",
                                  "message": f"Commander fire order: '{track_id}' is not a known track."})
            return None
        self._coa_seq += 1
        coa = COA(id=f"CMD-{self._coa_seq:03d}", action="request_fires", target=track_id,
                  asset=asset.entity_id, description="Commander-directed fire mission",
                  confidence=1.0, expected_success=1.0)
        self.approval_queue.append(coa)
        self.approve_coa(coa.id)
        return coa.id

    @_locked
    def reject_coa(self, coa_id: str):
        """Human rejects a COA without executing it."""
        self.approval_queue = [c for c in self.approval_queue if c.id != coa_id]
        self.aip_feed.append({"type": "COMMANDER", "message": f"Rejected {coa_id}"})
        if not self.approval_queue:
            self.paused = False

    def modify_plan(self, text: str):
        """Human provides natural language modification. Trigger replan."""
        self.approval_queue.clear()  # discard old COAs
        self.aip_feed.append({"type": "COMMANDER", "message": text})
        self.paused = False
        self._orient_and_decide(user_modification=text)

    @_locked
    def get_client_state(self):
        """Return Fog of War compliant state for the client."""
        return {
            "tick": self.current_tick,
            "blue": [e.position for e in self.world.blue if e.alive],
            "blue_details": [{"id": e.entity_id, "cls": e.cls, "pos": e.position}
                             for e in self.world.blue if e.alive],
            "tracks": self.fusion.snapshot(),  # ONLY fused tracks. True red is HIDDEN.
            "feed": self.aip_feed[-10:],       # last 10 messages
            "approval_queue": [c.__dict__ for c in self.approval_queue],
            "paused": self.paused,
            # Phase D: the enemy band the client blurs until recon reveals tracks,
            # plus the activated battlefield's identity (region/location/climate).
            "enemy_area": getattr(self.world, "enemy_area", None),
            "battlefield": getattr(self.world, "battlefield", None),
        }
