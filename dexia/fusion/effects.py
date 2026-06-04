"""EffectResolver — turn governed commands into real equipment effects (C5).

The far end of the kill chain: an ActionType the AI submitted (request_fires /
jam / task_isr), once it clears the ActionBus funnel, lands here and *operates
the actual platform* against the WorldState — using the equipment's catalog
effect spec (range, lethal radius, time-of-flight). This is what makes "the AI
commands real equipment" concrete rather than advisory (DESIGN_AIP.md 6.3).

Effects can be instantaneous (jam, isr reposition) or delayed (indirect fire has
a time-of-flight); delayed effects are scheduled and applied by ``step()``. Every
resolution returns an ``EffectEvent`` for the reasoning trace / HUD.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..physics3d import BallisticEngine, MissileEngine, clear_los, distance3d

JAM_SUPPRESS_R = 500.0        # how near the aimpoint a jammer kills an emission
JAM_DURATION = 40             # ticks an emitter stays suppressed
ISR_REPOSITION_SPEED = 120.0  # m/s — an ISR asset redirected to confirm a track


def _dist(a, b) -> float:
    """2D distance (kept for any legacy caller). Effect math uses ``distance3d``."""
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


@dataclass
class EffectEvent:
    tick: int
    action: str
    status: str                            # launched|impact|suppressed|tasked|rejected|no_effect
    asset_id: str = ""
    detail: str = ""
    killed: list = field(default_factory=list)
    trajectory: list = field(default_factory=list)   # 3D ballistic arc (P4; HUD)

    def to_dict(self) -> dict:
        return {"tick": self.tick, "action": self.action, "status": self.status,
                "asset_id": self.asset_id, "detail": self.detail, "killed": self.killed,
                "trajectory": self.trajectory}


class EffectResolver:
    def __init__(self, world, catalog=None) -> None:
        self.world = world
        self.cat = catalog or world.catalog
        self._pending: list = []           # [(impact_tick, fn(now)->EffectEvent)]

    # ---- entry points ---------------------------------------------------- #
    def submit(self, command: dict, tick: int) -> Optional[EffectEvent]:
        """Resolve one drained command. Returns None for legacy non-wargame
        commands (spawn/arm/...) so the existing streamer path is untouched."""
        fn = getattr(self, f"_do_{command.get('action', '')}", None)
        return fn(command, tick) if fn else None

    def step(self, tick: int) -> list:
        """Apply any delayed effects (e.g. rounds-in-flight) now due."""
        events, pending = [], []
        for due, fn in self._pending:
            (events.append(fn(tick)) if tick >= due else pending.append((due, fn)))
        self._pending = pending
        return events

    # ---- effect handlers ------------------------------------------------- #
    def _do_fire(self, cmd: dict, tick: int) -> EffectEvent:
        asset = self.world.get(cmd.get("asset_id"))
        if asset is None or not asset.alive:
            return EffectEvent(tick, "fire", "rejected", detail="no such asset")
        spec = self.cat.get(asset.cls)
        eff = spec.effect("indirect_fire") if spec else None
        if eff is None:
            return EffectEvent(tick, "fire", "rejected", asset.entity_id, "no indirect_fire")
        if asset.ammo <= 0:
            return EffectEvent(tick, "fire", "rejected", asset.entity_id, "winchester (no ammo)")
        tgt2 = cmd.get("target") or [cmd.get("x"), cmd.get("y")]
        terrain = getattr(self.world, "terrain", None)
        tz = terrain.height(float(tgt2[0]), float(tgt2[1])) if terrain is not None else 0.0
        aim = [float(tgt2[0]), float(tgt2[1]), tz]      # 3D aimpoint on the surface
        d = distance3d(asset.position, aim)
        if not eff.in_range(d):
            return EffectEvent(tick, "fire", "rejected", asset.entity_id,
                               f"out of range {int(d)}m not in [{int(eff.min_range_m)},{int(eff.range_m)}]")
        asset.ammo -= 1
        impact = tick + max(1, int(round(eff.tof_s)))
        # a real 3D arc for the HUD (only on terrain worlds; flat stays a no-op)
        traj = (BallisticEngine().trajectory(asset.position, aim, eff.tof_s)
                if terrain is not None else [])

        def land(now: int) -> EffectEvent:
            killed = [e.entity_id for e in self.world.red
                      if e.alive and distance3d(e.position, aim) <= eff.lethal_r]
            for e in self.world.red:
                if e.entity_id in killed:
                    e.alive = False
            return EffectEvent(now, "fire", "impact", asset.entity_id,
                               f"{len(killed)} neutralized @ [{round(aim[0])},{round(aim[1])}]",
                               killed)

        self._pending.append((impact, land))
        return EffectEvent(tick, "fire", "launched", asset.entity_id,
                           f"rounds inbound tof {int(eff.tof_s)}s -> impact t={impact} (ammo {int(asset.ammo)})",
                           trajectory=traj)

    def _do_strike(self, cmd: dict, tick: int) -> EffectEvent:
        """Loitering-munition strike — terminal homing, so it tracks the nearest
        target near the aimpoint (forgiving of lead error), expends the munition."""
        asset = self.world.get(cmd.get("asset_id"))
        if asset is None or not asset.alive:
            return EffectEvent(tick, "strike", "rejected", detail="no such asset")
        spec = self.cat.get(asset.cls)
        eff = spec.effect("loiter_strike") if spec else None
        if eff is None:
            return EffectEvent(tick, "strike", "rejected", asset.entity_id, "no loiter_strike")
        if asset.ammo <= 0:
            return EffectEvent(tick, "strike", "rejected", asset.entity_id, "expended")
        target = cmd.get("target") or [cmd.get("x"), cmd.get("y")]
        if distance3d(asset.position, target) > eff.range_m:
            return EffectEvent(tick, "strike", "rejected", asset.entity_id, "target beyond reach")
        asset.ammo -= 1                     # one-shot munition consumed
        impact = tick + max(1, int(round(eff.tof_s)))
        capture = eff.lethal_r + 120.0      # terminal seeker capture radius

        def land(now: int) -> EffectEvent:
            cands = [e for e in self.world.red if e.alive and distance3d(e.position, target) <= capture]
            killed = []
            if cands:
                tgt = min(cands, key=lambda e: _dist(e.position, target))
                tgt.alive = False
                killed = [tgt.entity_id]
            return EffectEvent(now, "strike", "impact", asset.entity_id,
                               f"{len(killed)} neutralized @ [{round(target[0])},{round(target[1])}]",
                               killed)

        self._pending.append((impact, land))
        return EffectEvent(tick, "strike", "launched", asset.entity_id,
                           f"loiter munition committed tof {int(eff.tof_s)}s -> impact t={impact}")

    def _do_jam(self, cmd: dict, tick: int) -> EffectEvent:
        asset = self.world.get(cmd.get("asset_id"))
        if asset is None or not asset.alive:
            return EffectEvent(tick, "jam", "rejected", detail="no such asset")
        spec = self.cat.get(asset.cls)
        eff = spec.effect("jam") if spec else None
        if eff is None:
            return EffectEvent(tick, "jam", "rejected", asset.entity_id, "no jam capability")
        target = cmd.get("target") or [cmd.get("x"), cmd.get("y")]
        if target[0] is None:
            return EffectEvent(tick, "jam", "rejected", asset.entity_id, "no aimpoint")
        if distance3d(asset.position, target) > eff.range_m:
            return EffectEvent(tick, "jam", "rejected", asset.entity_id, "aimpoint beyond jammer range")
        hit = []
        for e in self.world.red:
            espec = self.cat.get(e.cls)
            if e.alive and espec and espec.emits and distance3d(e.position, target) <= JAM_SUPPRESS_R:
                e.suppressed_until = tick + JAM_DURATION
                e.emitting = False
                hit.append(e.entity_id)
        if not hit:
            return EffectEvent(tick, "jam", "no_effect", asset.entity_id, "no emitter at aimpoint")
        return EffectEvent(tick, "jam", "suppressed", asset.entity_id,
                           f"emitters off {JAM_DURATION}t: {hit}", hit)

    def _do_isr(self, cmd: dict, tick: int) -> EffectEvent:
        asset = self.world.get(cmd.get("asset_id"))
        if asset is None or not asset.alive:
            return EffectEvent(tick, "isr", "rejected", detail="no such asset")
        x, y = cmd.get("x"), cmd.get("y")
        if x is None or y is None:
            return EffectEvent(tick, "isr", "rejected", asset.entity_id, "no area")
        # reposition the sensor platform over the area; its feed then covers it
        asset.route = [[float(x), float(y)]]
        asset._leg = 0
        asset.behavior = "advance"
        asset.speed_mps = ISR_REPOSITION_SPEED
        # on the 3D physics path the platform flies/drives itself there — re-aim
        # its motion model (legacy scripted path is untouched when _motion is None)
        if getattr(asset, "_motion", None) is not None:
            asset._motion.retarget([float(x), float(y)])
        return EffectEvent(tick, "isr", "tasked", asset.entity_id,
                           f"repositioning to [{round(float(x))},{round(float(y))}] to confirm")


# --------------------------------------------------------------------------- #
# Air-defense engagement (P4) — the mechanism + a driven resolver. Kept as module
# functions (not wired into MissionRunner): red SAM autonomy in the live loop is
# tactical (P5). The 3-check gate is what the verification scenario asserts.
# --------------------------------------------------------------------------- #
def sam_can_engage(sam_pos, effect, target_pos, terrain=None) -> dict:
    """Range ∧ altitude ∧ line-of-sight gate for a SAM vs an air target.
    Returns each check plus the combined ``engage`` verdict."""
    d = distance3d(sam_pos, target_pos)
    alt = float(target_pos[2]) if len(target_pos) > 2 else 0.0
    rng_ok = effect.in_range(d)
    alt_ok = effect.min_alt_m <= alt <= effect.max_alt_m
    los_ok = clear_los(terrain, sam_pos, target_pos)
    return {"range": rng_ok, "altitude": alt_ok, "los": los_ok,
            "engage": bool(rng_ok and alt_ok and los_ok),
            "dist_m": round(d, 1), "alt_m": round(alt, 1)}


def engage_air(sam_pos, effect, target_pos, target_vel=(0.0, 0.0, 0.0), *,
               terrain=None, missile_speed: float = 800.0) -> dict:
    """Resolve a SAM shot: gate on the 3 checks, then fly a PN ``MissileEngine`` to
    closest approach. ``hit`` is the miss distance within the warhead ``lethal_r``."""
    chk = sam_can_engage(sam_pos, effect, target_pos, terrain)
    if not chk["engage"]:
        return {"engaged": False, "checks": chk}
    miss = MissileEngine(speed=missile_speed).intercept(sam_pos, target_pos, target_vel)
    return {"engaged": True, "checks": chk,
            "miss_m": round(miss, 1), "hit": miss <= effect.lethal_r}
