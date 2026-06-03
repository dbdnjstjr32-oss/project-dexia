"""File-backed C2 command queue (HUD -> Python backend).

The Next.js ``/api/command`` route appends user actions (spawn/remove) to
``commands.json`` at the project root. The Python simulation loop polls and
consumes them each tick via :func:`drain_commands`, which removes only the
commands it processed — concurrent appends from the API are preserved.

Each command is a dict, e.g.::

    {"id": "...", "action": "spawn",  "x": 6.0, "y": 6.0, "z": 1.5,
     "lon": 126.97, "lat": 37.56, "profile": {...}}
    {"id": "...", "action": "remove", "agent_id": "agent_kami_5"}

Design goals: never block the sim loop, never raise on a malformed/locked file.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "commands.json",
)


def _read(path: str) -> list[dict]:
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _atomic_write(path: str, items: list[Any]) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def append_command(cmd: dict, path: str = DEFAULT_PATH) -> None:
    """Append one command (used by tests / Python-side producers)."""
    items = _read(path)
    items.append(cmd)
    _atomic_write(path, items)


def drain_commands(path: str = DEFAULT_PATH) -> list[dict]:
    """Return all pending commands and remove exactly those from the file.

    Re-reads the file before rewriting so commands appended concurrently (by the
    API) between the read and the write are not lost.
    """
    pending = _read(path)
    if not pending:
        return []
    processed_ids = {c.get("id") for c in pending if isinstance(c, dict)}
    # Re-read; keep anything that arrived after our snapshot.
    current = _read(path)
    remaining = [c for c in current if c.get("id") not in processed_ids]
    _atomic_write(path, remaining)
    return [c for c in pending if isinstance(c, dict)]
