"""InMemoryRegistry — OSS (Object Set Service) role: the indexed instance store.

Thread-safe in-memory store of ontology objects + links, with upsert/query and a
JSON snapshot for OAG context. Designed so the (optional) serialization step can
run off the physics hot loop. Can later be swapped for a SQLite-backed store
without changing callers.
"""

from __future__ import annotations

import threading
from typing import Any

from .schema import object_id


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
