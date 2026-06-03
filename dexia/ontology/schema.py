"""DroneOntology schema — OMS (Ontology Metadata Service) role.

The semantic source-of-truth: typed Object definitions + Link (edge) definitions
+ Action types. This promotes Dexia's flat ``telemetry.json`` into a structured
object graph that OAG (Phase 8) can feed to the LLM as typed context instead of
unstructured text.

Pure dataclasses (stdlib only) — no Ray / MuJoCo, so the ontology layer runs on
either interpreter and never touches the physics hot loop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# ---- OMS metadata: the declarative schema (Palantir OMS analogue) ---------- #
OBJECT_TYPES = ("DroneObject", "ThreatObject", "MissionObject")
LINK_TYPES = ("KillChainLink", "CommsLink")
ACTION_TYPES = ("move", "broadcast", "engage", "abort",
                "deploy", "recall", "activate", "standby")

DRONE_STATUS = ("staged", "flying", "active", "lost")


@dataclass
class DroneObject:
    agent_id: str
    role: str = ""
    kind: str = "kami"                 # recon | kami
    position: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    velocity: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    alt: float = 0.0
    speed: float = 0.0
    snr_db: float = 0.0
    link_good: bool = True
    status: str = "active"             # staged | flying | active | lost
    loss_reason: Optional[str] = None
    equipment: str = "Standard Quad"
    object_type: str = "DroneObject"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThreatObject:
    aa_id: str
    position: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    radar_range: float = 0.0
    engagement_range: float = 0.0
    ew_range: float = 0.0
    threat_level: str = "idle"         # idle | radar | kill
    ammo: int = 0
    max_ammo: int = 0
    tracked: list = field(default_factory=list)
    active_zones: int = 0
    object_type: str = "ThreatObject"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MissionObject:
    mission_id: str = "mission_0"
    tick: int = 0
    scenario: str = "interactive"
    armed: bool = False
    broadcast: bool = False
    kill_confirmed: bool = False
    network_survivability: float = 1.0
    total_lost: int = 0
    object_type: str = "MissionObject"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KillChainLink:
    source: str                        # recon id
    target: str                        # kami id or 'enemy_target'
    confirmed_at_tick: Optional[int] = None
    link_type: str = "KillChainLink"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CommsLink:
    drone_id: str
    base: str = "base_station"
    snr_db: float = 0.0
    packet_lost: bool = False
    state: str = "GOOD"                # GOOD | BAD
    link_type: str = "CommsLink"

    def to_dict(self) -> dict:
        return asdict(self)


def object_id(obj) -> str:
    """The natural key for an ontology object."""
    for attr in ("agent_id", "aa_id", "mission_id"):
        v = getattr(obj, attr, None)
        if v is not None:
            return v
    return id(obj)  # fallback
