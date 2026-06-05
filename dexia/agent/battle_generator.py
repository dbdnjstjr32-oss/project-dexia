"""Procedural Battle Generator.

Replaces static YAML scenarios by generating a battlefield based on user inputs.
Produces a valid `Scenario` and `WorldState`.
"""

import math
import random
from typing import Dict, List

from dexia.scenario.scenario import Scenario, Mission, ForceElement
from dexia.fusion.world import WorldState
from dexia.scenario.catalog import get_catalog

class BattleGenerator:
    def __init__(self, seed: int = None):
        self.rng = random.Random(seed)
        self.catalog = get_catalog()

    def from_battlefield(self, bf: dict) -> WorldState:
        """Activate a battlefield from the catalog: region terrain + map size, with
        blue/red equipment randomly populated onto it (Phase D)."""
        from dexia.scenario.battlefields import terrain_spec, REGIONS
        terrain = terrain_spec(bf["terrain_shape"], bf["size_m"])
        world = self.generate(bf["blue"], bf.get("difficulty", "medium"),
                              map_size=bf["size_m"], terrain=terrain)
        world.battlefield = {
            "id": bf["id"], "region": bf["region"], "name": bf["name"],
            "location": bf["location"], "size_m": bf["size_m"],
            "climate": bf.get("climate", REGIONS.get(bf["region"], {}).get("climate", "")),
        }
        return world

    def generate(self, blue_forces: Dict[str, int], difficulty: str = "medium",
                 map_size: int = 10000, terrain: dict = None) -> WorldState:
        """Generate a procedural battle."""
        scenario_id = f"proc_battle_{self.rng.randint(1000, 9999)}"
        
        # Determine Red force based on difficulty
        # simple heuristic for now
        multiplier = {"easy": 0.5, "medium": 1.0, "hard": 2.0}.get(difficulty, 1.0)
        
        red_pool = [
            ("bmp_apc", int(4 * multiplier)),
            ("t72_tank", int(3 * multiplier)),
            ("sa11_sam", int(2 * multiplier)),
            ("krasukha_ew", int(1 * multiplier)),
        ]
        
        blue_elements = []
        # Blue spawns in the south (y = -4000 to -2000)
        for cls, count in blue_forces.items():
            if count <= 0: continue
            spec = self.catalog.get(cls)
            if not spec: continue
            
            x = self.rng.uniform(-2000, 2000)
            y = self.rng.uniform(-map_size/2, -map_size/4)
            blue_elements.append(
                ForceElement(cls=cls, n=count, pos=[x, y], behavior="static")
            )
            
        red_elements = []
        # Red spawns in the north (y = 1000 to map edge); record the band so the
        # HUD can fog/blur the whole enemy area until recon reveals it.
        red_x_span = (-3000.0, 3000.0)
        red_y_span = (1000.0, map_size / 2.0)
        for cls, count in red_pool:
            if count <= 0: continue
            x = self.rng.uniform(*red_x_span)
            y = self.rng.uniform(*red_y_span)
            
            behavior = "static_ad" if "sam" in cls or "ew" in cls else "static"
            
            red_elements.append(
                ForceElement(cls=cls, n=count, pos=[x, y], behavior=behavior)
            )
            
        mission = Mission(
            tasking="Find and destroy enemy forces.",
            intent="destroy",
            victory={"max_blue_loss": 5}
        )
        
        scenario = Scenario(
            id=scenario_id,
            theater="procedural_hills",
            mission=mission,
            blue=blue_elements,
            red=red_elements,
            feeds=["uav_eo", "sigint", "gsr"],
            terrain=terrain or {"type": "hill", "peak": 400.0, "sigma": 1500.0,
                                "size": 101, "dx": 100.0},
        )
        
        world = WorldState.from_scenario(scenario.require_valid(self.catalog), self.catalog)
        # keep the originating scenario so downstream (MissionManager) can read the
        # declared feed set instead of assuming the default recon triad.
        world.scenario = scenario
        # the enemy band the HUD blurs until recon (honest: the region, not the
        # exact hidden unit positions).
        world.enemy_area = {
            "x0": red_x_span[0] - 500.0, "x1": red_x_span[1] + 500.0,
            "y0": red_y_span[0] - 200.0, "y1": red_y_span[1] + 500.0,
        }
        return world

