// C2 command queue storage — appends user actions (spawn/remove) to the shared
// commands.json at the project root, which the Python streamer drains each tick.

import fs from 'fs';
import path from 'path';
import crypto from 'crypto';

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

function writeQueue(items) {
  const dir = path.dirname(COMMANDS_PATH);
  const tmp = path.join(dir, `.commands.${process.pid}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(items), 'utf-8');
  fs.renameSync(tmp, COMMANDS_PATH); // atomic
}

// Append one command; auto-assigns id + timestamp. Returns the stored command.
export function enqueueCommand(cmd) {
  const stored = {
    id: 'cmd_' + crypto.randomUUID().replace(/-/g, '').slice(0, 14),
    ts: new Date().toISOString(),
    ...cmd,
  };
  const q = readQueue();
  q.push(stored);
  writeQueue(q);
  return stored;
}

export { COMMANDS_PATH, readQueue };
