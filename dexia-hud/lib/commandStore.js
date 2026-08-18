// C2 command queue storage — READ-ONLY view of the shared commands.json that the
// Python streamer drains each tick.
//
// Writes go EXCLUSIVELY through the governed FastAPI control plane (/api/sim/*).
// The HUD never writes commands.json directly: that legacy path bypassed the
// clearance check, the ActionBus ontology guards, and the immutable lineage
// trail (and did not share the Python cross-process lock). It has been removed.

import fs from 'fs';
import path from 'path';

const COMMANDS_PATH =
  process.env.DRONE_COMMANDS_PATH || path.join(process.cwd(), '..', 'commands.json');

function readQueue() {
  try {
    if (!fs.existsSync(COMMANDS_PATH)) return [];
    const data = JSON.parse(fs.readFileSync(COMMANDS_PATH, 'utf-8'));
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export { COMMANDS_PATH, readQueue };
