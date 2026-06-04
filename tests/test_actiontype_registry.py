"""ActionType registry is the single write-funnel source of truth.

Guards the invariant that the agent's tools, the canonical action types, and the
command-queue side effects are all generated from ONE registry — the property
whose violation let the HUD approve path bypass governance.
"""

from __future__ import annotations

from dexia.ontology import schema
from dexia.ontology.actions import (
    ACTION_REGISTRY,
    TOOL_TO_ACTION,
    clearance_ok,
    ollama_tools,
    to_api_call,
    to_command,
)


def test_registry_covers_schema_action_types():
    # Every canonical schema action type has an ActionType definition.
    for name in schema.ACTION_TYPES:
        assert name in ACTION_REGISTRY, f"missing ActionType for '{name}'"


def test_ollama_tools_generated_from_registry():
    tools = ollama_tools()
    names = {t["function"]["name"] for t in tools}
    # tools are exactly the tool_name-bearing actions, no hand-written drift.
    assert names == set(TOOL_TO_ACTION)
    # deploy_drone exposes required x,y just like the endpoint expects.
    deploy = next(t for t in tools if t["function"]["name"] == "deploy_drone")
    assert deploy["function"]["parameters"]["required"] == ["x", "y"]


def test_to_command_maps_to_queue_actions():
    assert to_command("deploy", {"x": 1, "y": 2})["action"] == "spawn"
    assert to_command("recall", {"agent_id": "a"})["action"] == "remove"
    assert to_command("activate", {})["action"] == "arm"
    assert to_command("standby", {})["action"] == "disarm"
    assert to_command("engage", {}) is None  # not a queue-backed side effect


def test_to_api_call_routes_to_governed_endpoint():
    call = to_api_call({"tool": "deploy_drone", "kwargs": {"x": 3, "y": 4}})
    assert call["path"] == "/api/sim/deploy"
    assert call["body"] == {"x": 3, "y": 4}
    assert call["action"] == "deploy"


def test_clearance_lattice():
    assert clearance_ok("commander", "operator")
    assert clearance_ok("operator", "operator")
    assert not clearance_ok("operator", "commander")
    assert not clearance_ok(None, "operator")


def test_clearance_required_per_action():
    assert ACTION_REGISTRY["activate"].required_clearance == "commander"
    assert ACTION_REGISTRY["clear"].required_clearance == "commander"
    assert ACTION_REGISTRY["deploy"].required_clearance == "operator"
