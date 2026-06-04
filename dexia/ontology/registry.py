"""InMemoryRegistry — OSS (Object Set Service) role: the indexed instance store.

Thread-safe in-memory store of ontology objects + links, with upsert/query and a
JSON snapshot for OAG context. Designed so the (optional) serialization step can
run off the physics hot loop. Can later be swapped for a SQLite-backed store
without changing callers.
"""

from __future__ import annotations

import threading
from typing import Any

from .schema import DroneObject, Position, ThreatObject, object_id


class InMemoryRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.objects: dict[str, dict[str, Any]] = {}   # type -> {id: obj}
        self.links: list = []

    # ---- writes ---------------------------------------------------------- #
    def upsert(self, obj) -> None:
        with self._lock:
            self.objects.setdefault(obj.object_type, {})[object_id(obj)] = obj

    def replace(self, object_type: str, objs: list) -> None:
        """Replace the whole set for a type (snapshot semantics — prunes stale)."""
        with self._lock:
            self.objects[object_type] = {object_id(o): o for o in objs}

    def set_links(self, links: list) -> None:
        with self._lock:
            self.links = list(links)

    def clear(self) -> None:
        with self._lock:
            self.objects = {}
            self.links = []

    # ---- reads ----------------------------------------------------------- #
    def get(self, object_type: str, oid: str):
        with self._lock:
            return self.objects.get(object_type, {}).get(oid)

    def all(self, object_type: str) -> list:
        with self._lock:
            return list(self.objects.get(object_type, {}).values())

    def query(self, object_type: str, **filters) -> list:
        with self._lock:
            out = []
            for o in self.objects.get(object_type, {}).values():
                if all(getattr(o, k, None) == v for k, v in filters.items()):
                    out.append(o)
            return out

    def links_of(self, link_type: str) -> list:
        with self._lock:
            return [l for l in self.links if getattr(l, "link_type", None) == link_type]

    def count(self, object_type: str) -> int:
        with self._lock:
            return len(self.objects.get(object_type, {}))

    def snapshot(self) -> dict:
        """Full ontology as plain JSON (the OAG context payload)."""
        with self._lock:
            return {
                "objects": {
                    t: [o.to_dict() for o in d.values()]
                    for t, d in self.objects.items()
                },
                "links": [
                    l.to_dict() if hasattr(l, "to_dict") else l for l in self.links
                ],
            }


# --------------------------------------------------------------------------- #
# Phase 6.1 — OntologyRegistry: a focused active-object store with geometry
# queries. Sits alongside InMemoryRegistry (which the live streamer uses); this
# one is the clean, spec-facing surface for the data-transformation PoC.
# --------------------------------------------------------------------------- #
def _to_position(p) -> Position:
    return p if isinstance(p, Position) else Position.from_list(p)


class OntologyRegistry:
    """In-memory store of the current Semantic Objects with typed query methods."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.drones: dict[str, DroneObject] = {}
        self.threats: dict[str, ThreatObject] = {}

    # ---- writes ---------------------------------------------------------- #
    def load(self, objects: list) -> "OntologyRegistry":
        """Replace the active set from a flat list of Semantic Objects."""
        with self._lock:
            self.drones, self.threats = {}, {}
            for o in objects or []:
                self.upsert(o)
        return self

    def upsert(self, obj) -> None:
        with self._lock:
            if isinstance(obj, DroneObject):
                self.drones[obj.agent_id] = obj
            elif isinstance(obj, ThreatObject):
                self.threats[obj.aa_id] = obj

    def clear(self) -> None:
        with self._lock:
            self.drones, self.threats = {}, {}

    # ---- queries --------------------------------------------------------- #
    def all_drones(self) -> list:
        with self._lock:
            return list(self.drones.values())

    def all_threats(self) -> list:
        with self._lock:
            return list(self.threats.values())

    def get_active_drones(self) -> list:
        """Drones that are not lost."""
        with self._lock:
            return [d for d in self.drones.values() if d.active]

    def get_threats_in_range(self, position, radius: float) -> list:
        """Threats whose position lies within ``radius`` of ``position``."""
        p = _to_position(position)
        with self._lock:
            return [t for t in self.threats.values() if t.xyz().distance_to(p) <= radius]

    def get_drones_in_range(self, center, radius: float, *, active_only: bool = False) -> list:
        """Drones within ``radius`` of ``center`` (e.g. a threat's kill radius)."""
        c = _to_position(center)
        with self._lock:
            drones = self.drones.values()
            hits = [d for d in drones if d.xyz().distance_to(c) <= radius]
            return [d for d in hits if d.active] if active_only else hits

    def get_drones_threatened_by(self, threat: ThreatObject, *, active_only: bool = True) -> list:
        """Drones inside a given threat's lethal radius — the endangered set."""
        return self.get_drones_in_range(threat.xyz(), threat.lethal_radius(),
                                        active_only=active_only)
