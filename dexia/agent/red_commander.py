"""Red Commander (Enemy AI).

Implements basic doctrine and maneuvers for Red forces during the live simulation.
"""

import math
import random
from typing import List

from dexia.fusion.world import WorldState, Entity

class RedCommander:
    def __init__(self, seed: int = None):
        self.rng = random.Random(seed)
        self.tick_counter = 0

    def step(self, world: WorldState, tick: int):
        """Called every cycle to allow Red to maneuver based on Blue proximity."""
        self.tick_counter += 1
        if self.tick_counter % 5 != 0:
            return
            
        red_units = world.red
        blue_units = world.blue
        
        for r_unit in red_units:
            if not r_unit.alive: continue
            
            # Find closest blue unit
            closest_blue = None
            min_dist = float('inf')
            for b_unit in blue_units:
                if not b_unit.alive: continue
                dist = math.hypot(r_unit.position[0] - b_unit.position[0], r_unit.position[1] - b_unit.position[1])
                if dist < min_dist:
                    min_dist = dist
                    closest_blue = b_unit
                    
            if r_unit.category in ("armor", "mechanized", "apc"):
                # If Blue is too close (< 2000m), fall back (retreat north)
                if min_dist < 2000:
                    r_unit.route = [[r_unit.position[0], r_unit.position[1] + 1000]]
                    r_unit.behavior = "advance"
                    r_unit._leg = 0
                # If Blue is far, maybe flank or patrol
                elif self.rng.random() < 0.1 and (r_unit.behavior == "static" or r_unit.route is None):
                    dx = self.rng.uniform(-500, 500)
                    dy = self.rng.uniform(-500, 500)
                    r_unit.route = [[r_unit.position[0] + dx, r_unit.position[1] + dy]]
                    r_unit.behavior = "advance"
                    r_unit._leg = 0
                    
            # AD radars occasionally toggle on/off to avoid SIGINT, or turn off if Blue is very close
            if r_unit.category == "air_defense":
                if min_dist < 3000:
                    r_unit.emitting = False # hide
                elif self.rng.random() < 0.2:
                    r_unit.emitting = not r_unit.emitting
