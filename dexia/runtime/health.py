"""HealthMonitor — liveness watchdog (Phase 10).

The simulator's heartbeat is the telemetry it emits every tick. HealthMonitor
reads that heartbeat (``telemetry.json`` ``time``/``tick``) and flags a *stall*
when the snapshot stops advancing for longer than ``stall_seconds`` — the signal
a supervisor (or docker ``restart: unless-stopped``) uses to bounce a wedged
streamer. It also keeps a light registry of per-service heartbeats so the
``/api/health`` endpoint can report the whole stack at a glance.

Pure stdlib; the clock is injectable so the stall logic is unit-testable without
real wall-time sleeps.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_TELEMETRY_PATH = os.path.join(_ROOT, "telemetry.json")


def _parse_iso(ts: str) -> Optional[float]:
    """ISO-8601 -> epoch seconds (tolerant of a trailing 'Z')."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


class HealthMonitor:
    def __init__(self, telemetry_path: str = DEFAULT_TELEMETRY_PATH,
                 stall_seconds: float = 5.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.telemetry_path = telemetry_path
        self.stall_seconds = float(stall_seconds)
        self._clock = clock
        self._services: dict[str, dict] = {}
        self._last_tick: Optional[int] = None
        self._last_tick_at: Optional[float] = None

    # ---- service registry (for the API aggregator) -------------------- #
    def beat(self, name: str, ok: bool = True, detail: str = "") -> None:
        self._services[name] = {"ok": bool(ok), "detail": detail, "at": self._clock()}

    # ---- the sim heartbeat ------------------------------------------- #
    def sim_status(self) -> dict:
        """Liveness of the telemetry stream: present? fresh? advancing?"""
        now = self._clock()
        if not os.path.exists(self.telemetry_path):
            return {"ok": False, "state": "absent", "reason": "telemetry.json not found",
                    "tick": None, "age_s": None, "stalled": True}
        try:
            with open(self.telemetry_path, "r", encoding="utf-8") as f:
                telem = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return {"ok": False, "state": "unreadable", "reason": str(e),
                    "tick": None, "age_s": None, "stalled": True}

        tick = telem.get("tick")
        # track when the tick last changed (advance check, robust to a frozen clock)
        if tick != self._last_tick:
            self._last_tick = tick
            self._last_tick_at = now

        emitted = _parse_iso(telem.get("time", "")) if telem.get("time") else None
        age = (now - emitted) if emitted is not None else (
            (now - self._last_tick_at) if self._last_tick_at is not None else None)
        stalled = age is not None and age > self.stall_seconds

        return {
            "ok": not stalled,
            "state": "stalled" if stalled else "live",
            "tick": tick,
            "age_s": round(age, 2) if age is not None else None,
            "stalled": stalled,
            "reason": f"no tick for {age:.1f}s > {self.stall_seconds}s" if stalled else "",
        }

    def stalled(self) -> bool:
        return self.sim_status()["stalled"]

    # ---- whole-stack rollup ------------------------------------------ #
    def report(self) -> dict:
        sim = self.sim_status()
        services = {
            name: {**s, "stale": (self._clock() - s["at"]) > self.stall_seconds}
            for name, s in self._services.items()
        }
        all_ok = sim["ok"] and all(v["ok"] and not v["stale"] for v in services.values())
        return {
            "ok": all_ok,
            "ts": datetime.now(timezone.utc).isoformat(),
            "sim": sim,
            "services": services,
        }
