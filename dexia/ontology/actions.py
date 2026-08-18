"""ActionType registry — the single write-funnel source of truth (AIP Funnel).

Palantir AIP's defining principle: the *only* way to mutate the world is an
**Action Type** — a typed, validated, permissioned operation. Humans (HUD) and
agents (LLM tool calls) submit the SAME Action Types; nothing writes state by any
other path. This module is that registry.

Each ActionType bundles, in one place:
  * ``params``            — typed parameter schema (drives both validation AND the
                            Ollama function-calling ``tools`` the agent introspects)
  * ``required_clearance``— the minimum principal clearance to invoke it
  * ``validate``          — ontology-aware guard (LOST drone, kill-chain MAC, …)
  * ``side_effect``       — the concrete command enqueued on accept (or None)

Because the agent's tools, the ActionBus rules, and the command-queue mapping are
all *generated from this one registry*, they can never drift apart — which was
the bug that let the HUD approve path bypass governance entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# clearance lattice: operator < commander
CLEARANCE_RANK = {"operator": 1, "commander": 2}


class ActionRejected(Exception):
    """Raised by a validator when an action violates schema/state/MAC/clearance."""


@dataclass
class ActionType:
    name: str                                   # canonical action (schema.ACTION_TYPES)
    required_clearance: str = "operator"
    params: dict = field(default_factory=dict)  # {param: {"type","required","desc"}}
    description: str = ""
    tool_name: Optional[str] = None             # Ollama function name (if agent-callable)
    validate: Optional[Callable] = None         # (registry, agent_id, payload) -> None
    side_effect: Optional[Callable] = None       # (payload) -> command dict | None

    def required_params(self) -> list[str]:
        return [k for k, v in self.params.items() if v.get("required")]


# --------------------------------------------------------------------------- #
# Reusable validators (ontology-aware). Each raises ActionRejected on violation.
# Ported from the old inline ActionBus._validate so display == enforcement.
# --------------------------------------------------------------------------- #
def _require_xy(registry, agent_id, payload) -> None:
    if not ({"x", "y"} <= set(payload or {})):
        raise ActionRejected("requires payload x, y")


def _not_lost(registry, agent_id, payload) -> None:
    if agent_id and registry is not None:
        drone = registry.get("DroneObject", agent_id)
        if drone is not None and getattr(drone, "status", None) == "lost":
            raise ActionRejected(f"agent {agent_id} is LOST — action denied")


def _recallable(registry, agent_id, payload) -> None:
    """A drone that is already LOST cannot be recalled — nothing to return."""
    if agent_id and registry is not None:
        drone = registry.get("DroneObject", agent_id)
        if drone is not None and getattr(drone, "status", None) == "lost":
            raise ActionRejected(f"agent {agent_id} already LOST — nothing to recall")


def _kami_mac(registry, agent_id, payload) -> None:
    """Kill-chain MAC: a kamikaze cannot 'engage' before the recon broadcast."""
    if not (agent_id and registry is not None):
        return
    drone = registry.get("DroneObject", agent_id)
    if drone is not None and getattr(drone, "kind", None) == "kami":
        missions = registry.all("MissionObject")
        broadcast = missions[0].broadcast if missions else False
        if not broadcast:
            raise ActionRejected(
                "kamikaze 'engage' denied before broadcast (kill-chain MAC)"
            )


def _chain(*validators):
    def run(registry, agent_id, payload):
        for v in validators:
            v(registry, agent_id, payload)
    return run


# ---- catalog-aware guards (wargame asset capability — Phase fusion) -------- #
def _asset_spec(asset_id):
    """Resolve a unit id (instance 'm777_howitzer_0' or class 'm777_howitzer')
    to its EquipmentSpec so capability guards can check what it can actually do."""
    if not asset_id:
        return None
    try:
        from ..scenario.catalog import get_catalog
    except Exception:
        return None
    cat = get_catalog()
    return cat.get(asset_id) or cat.get(re.sub(r"_\d+$", "", str(asset_id)))


def _has_effect(effect_type):
    """Guard: the named asset must possess ``effect_type`` in the catalog."""
    def run(registry, agent_id, payload):
        aid = (payload or {}).get("asset_id") or agent_id
        spec = _asset_spec(aid)
        if spec is not None and spec.effect(effect_type) is None:
            raise ActionRejected(f"asset '{aid}' has no '{effect_type}' capability")
    return run


def _require_target(registry, agent_id, payload) -> None:
    p = payload or {}
    if not (p.get("target") or p.get("target_id") or {"x", "y"} <= set(p)):
        raise ActionRejected("requires target [x,y] or target_id")


def _require_route(registry, agent_id, payload) -> None:
    p = payload or {}
    if not ((p.get("asset_id") or agent_id) and p.get("route")):
        raise ActionRejected("requires asset_id and a non-empty route")


# --------------------------------------------------------------------------- #
# Side effects — canonical action -> command-queue dict (drained by the streamer)
# --------------------------------------------------------------------------- #
def _deploy_cmd(p):
    return {"action": "spawn", "x": p.get("x"), "y": p.get("y"),
            "z": p.get("z", 0.3), "profile": p.get("profile"),
            "role": p.get("role", "kami"), "route": p.get("route")}

def _recall_cmd(p):
    return {"action": "remove", "agent_id": p.get("agent_id")}

def _enemy_cmd(p):
    return {"action": "set_enemy", "x": p.get("x"), "y": p.get("y")}

def _friendly_cmd(p):
    return {"action": "set_friendly", "x": p.get("x"), "y": p.get("y")}


def _fire_cmd(p):
    return {"action": "fire", "asset_id": p.get("asset_id"),
            "target": p.get("target") or [p.get("x"), p.get("y")],
            "target_id": p.get("target_id")}


def _isr_cmd(p):
    return {"action": "isr", "asset_id": p.get("asset_id"),
            "x": p.get("x"), "y": p.get("y"), "r": p.get("radius", 1000)}


def _recon_cmd(p):
    return {"action": "recon_route", "asset_id": p.get("asset_id"),
            "route": p.get("route") or [], "orbit": p.get("orbit"), "alt": p.get("alt")}


def _jam_cmd(p):
    return {"action": "jam", "asset_id": p.get("asset_id"),
            "target": p.get("target") or [p.get("x"), p.get("y")],
            "target_id": p.get("target_id")}


def _strike_cmd(p):
    # wargame loitering-munition strike (asset_id present); legacy drone engage
    # carries no asset_id and stays queue-less (governance-only) -> None.
    if not p.get("asset_id"):
        return None
    return {"action": "strike", "asset_id": p.get("asset_id"),
            "target": p.get("target") or [p.get("x"), p.get("y")],
            "target_id": p.get("target_id")}


# --------------------------------------------------------------------------- #
# THE registry — the single source of truth.
# --------------------------------------------------------------------------- #
ACTION_REGISTRY: dict[str, ActionType] = {
    "deploy": ActionType(
        name="deploy", required_clearance="operator", tool_name="deploy_drone",
        description="지정한 로컬 맵 좌표 (x, y) [m]에 아군 드론을 배치합니다. "
                    "role='kami'이면 자폭 타격 드론, 'recon'이면 정찰 드론으로 임무가 부여됩니다. "
                    "z는 초기 고도(기본 0.3m), profile은 기체 사양 딕셔너리(선택)입니다. "
                    "위협 대응 또는 전력 집중이 필요할 때 사용하십시오.",
        params={
            "x": {"type": "number", "required": True,
                  "desc": "로컬 X 좌표 [m]"},
            "y": {"type": "number", "required": True,
                  "desc": "로컬 Y 좌표 [m]"},
            "z": {"type": "number", "required": False,
                  "desc": "초기 고도 [m] (기본값 0.3 — 지상 스테이징)"},
            "role": {"type": "string", "required": False,
                     "desc": "드론 역할: 'kami'(자폭 타격) 또는 'recon'(정찰→방송). 기본값 'kami'.",
                     "enum": ["kami", "recon"]},
            "profile": {"type": "string", "required": False,
                        "desc": "기체 사양 프로파일 ID (Garage 등록 키). 생략 가능."},
            "route": {"type": "array", "required": False,
                      "desc": "우회 비행 경로 웨이포인트 리스트. 생략 가능."},
        },
        validate=_chain(_require_xy, _not_lost),
        side_effect=_deploy_cmd,
    ),
    "recall": ActionType(
        name="recall", required_clearance="operator", tool_name="recall_drone",
        description="Recall (return-to-base) a friendly drone by its agent_id, e.g. when it is exposed to the enemy AA kill zone.",
        params={"agent_id": {"type": "string", "required": True, "desc": "drone id"}},
        validate=_recallable, side_effect=_recall_cmd,
    ),
    "activate": ActionType(
        name="activate", required_clearance="commander", tool_name="activate",
        description="ARM the scenario: launch all staged drones (they take off under physics) and bring the engagement live. Commander-only escalation.",
        params={},
        side_effect=lambda p: {"action": "arm"},
    ),
    "standby": ActionType(
        name="standby", required_clearance="operator", tool_name="standby",
        description="DISARM: freeze drones in place and hold fire (de-escalate).",
        params={},
        side_effect=lambda p: {"action": "disarm"},
    ),
    "set_enemy": ActionType(
        name="set_enemy", required_clearance="operator",
        description="Mark/relocate the enemy AA strongpoint.",
        params={
            "x": {"type": "number", "required": True},
            "y": {"type": "number", "required": True},
        },
        validate=_require_xy, side_effect=_enemy_cmd,
    ),
    "set_friendly": ActionType(
        name="set_friendly", required_clearance="operator",
        description="Mark/relocate the friendly base.",
        params={
            "x": {"type": "number", "required": True},
            "y": {"type": "number", "required": True},
        },
        validate=_require_xy, side_effect=_friendly_cmd,
    ),
    "clear": ActionType(
        name="clear", required_clearance="commander",
        description="Recall all deployed drones and disarm.",
        params={},
        side_effect=lambda p: {"action": "clear"},
    ),
    # --- Heterogeneous asset tasking (fusion wargame: AI operates real kit) -- #
    "request_fires": ActionType(
        name="request_fires", required_clearance="commander", tool_name="request_fires",
        description="Call for indirect fire from an artillery/MLRS battery onto a ground target (x,y) or enemy track id. Use to mass fires on armor/columns beyond line-of-sight. asset_id = the firing battery.",
        params={
            "asset_id": {"type": "string", "required": True, "desc": "firing battery id"},
            "x": {"type": "number", "required": False, "desc": "target X [m]"},
            "y": {"type": "number", "required": False, "desc": "target Y [m]"},
            "target_id": {"type": "string", "required": False, "desc": "enemy track id"},
        },
        validate=_chain(_require_target, _has_effect("indirect_fire")),
        side_effect=_fire_cmd,
    ),
    "task_isr": ActionType(
        name="task_isr", required_clearance="operator", tool_name="task_isr",
        description="Task an ISR asset (recon UAV / sensor) to observe an area (x,y) — repositions its sensor to confirm a low-confidence track before committing a strike. asset_id = the ISR platform.",
        params={
            "asset_id": {"type": "string", "required": True, "desc": "ISR asset id"},
            "x": {"type": "number", "required": True, "desc": "area X [m]"},
            "y": {"type": "number", "required": True, "desc": "area Y [m]"},
            "radius": {"type": "number", "required": False, "desc": "search radius [m]"},
        },
        validate=_require_xy,
        side_effect=_isr_cmd,
    ),
    "recon_route": ActionType(
        name="recon_route", required_clearance="operator", tool_name="recon_route",
        description="Fly an ISR/recon asset along a planned multi-waypoint route over an "
                    "unknown/unconfirmed area, then loiter (orbit) to hold coverage. The route "
                    "is planned from terrain by the staff, not hand-authored. asset_id = the ISR "
                    "platform; route = [[x,y],...] waypoints; orbit = {center:[x,y], r, alt}.",
        params={
            "asset_id": {"type": "string", "required": True, "desc": "ISR asset id"},
            "route": {"type": "array", "required": True, "desc": "waypoints [[x,y],...]"},
            "orbit": {"type": "object", "required": False, "desc": "loiter circle {center,r,alt}"},
        },
        validate=_require_route,
        side_effect=_recon_cmd,
    ),
    "jam": ActionType(
        name="jam", required_clearance="operator", tool_name="jam",
        description="Direct an EW jammer to suppress an enemy emitter (SAM/EW radar) at a location — forces the radar off, opening a window for air assets. asset_id = the jammer.",
        params={
            "asset_id": {"type": "string", "required": True, "desc": "jammer id"},
            "x": {"type": "number", "required": False, "desc": "emitter X [m]"},
            "y": {"type": "number", "required": False, "desc": "emitter Y [m]"},
            "target_id": {"type": "string", "required": False, "desc": "emitter track id"},
        },
        validate=_chain(_require_target, _has_effect("jam")),
        side_effect=_jam_cmd,
    ),
    # Agent/MAC-level actions (not directly queue-backed in the current sim).
    "engage": ActionType(
        name="engage", required_clearance="commander",
        description="Lethal strike. Kill-chain MAC gated: a kamikaze may not engage before the recon broadcast.",
        params={}, validate=_chain(_not_lost, _kami_mac), side_effect=_strike_cmd,
    ),
    "move": ActionType(
        name="move", required_clearance="operator", params={
            "x": {"type": "number", "required": True},
            "y": {"type": "number", "required": True},
        },
        validate=_chain(_require_xy, _not_lost), side_effect=None,
    ),
    "broadcast": ActionType(name="broadcast", required_clearance="operator", side_effect=None),
    "abort": ActionType(name="abort", required_clearance="operator", side_effect=None),
}

# tool name (Ollama function) -> canonical action name
TOOL_TO_ACTION = {
    a.tool_name: a.name for a in ACTION_REGISTRY.values() if a.tool_name
}


def get_action(name: str) -> Optional[ActionType]:
    return ACTION_REGISTRY.get(name)


def clearance_ok(have: Optional[str], need: str) -> bool:
    return CLEARANCE_RANK.get(have or "", 0) >= CLEARANCE_RANK.get(need, 99)


def to_command(action_name: str, payload: dict) -> Optional[dict]:
    """The concrete command-queue dict for an accepted action (or None)."""
    at = ACTION_REGISTRY.get(action_name)
    if at is None or at.side_effect is None:
        return None
    return at.side_effect(payload or {})


# --------------------------------------------------------------------------- #
# Derived: Ollama function-calling tools (replaces the hand-written list).
# Generated from the registry so agent tools can never drift from the endpoints.
# --------------------------------------------------------------------------- #
def ollama_tools() -> list[dict]:
    tools = []
    for at in ACTION_REGISTRY.values():
        if not at.tool_name:
            continue
        props = {
            k: {"type": v.get("type", "string"),
                **({"description": v["desc"]} if v.get("desc") else {})}
            for k, v in at.params.items()
        }
        tools.append({
            "type": "function",
            "function": {
                "name": at.tool_name,
                "description": at.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": at.required_params(),
                },
            },
        })
    return tools


def to_api_call(rec: dict) -> dict:
    """Map a COA recommendation (tool call) to its control-API call, derived from
    the registry. Used by the HUD approve path so it routes through the funnel."""
    tool = rec.get("tool")
    kw = rec.get("kwargs", {}) or {}
    action = TOOL_TO_ACTION.get(tool)
    endpoint_map = {
        "deploy": ("/api/sim/deploy", {
            "x": kw.get("x"), "y": kw.get("y"),
            "z": kw.get("z", 0.3), "role": kw.get("role", "kami"),
            "profile": kw.get("profile"), "route": rec.get("route") or kw.get("route")
        }),
        "recall": ("/api/sim/recall", {"agent_id": kw.get("agent_id")}),
        "activate": ("/api/sim/activate", {}),
        "standby": ("/api/sim/standby", {}),
    }
    path, body = endpoint_map.get(action, ("/api/sim/standby", {}))
    return {"method": "POST", "path": path, "body": body, "action": action}
