// Drone Garage storage layer — reads/writes the shared `drone_profiles.json`
// at the project root (one directory up from /dexia-hud). This file is the
// shared state between the Next.js HUD and the Python backend's MuJoCo builder.
//
// Pure fs + JSON (no DB). Functions are exported so the API route stays thin and
// the storage logic is unit-testable without a running server.

import fs from 'fs';
import path from 'path';

const PROFILES_PATH =
  process.env.DRONE_PROFILES_PATH || path.join(process.cwd(), '..', 'drone_profiles.json');

const VALID_TOPOLOGIES = ['quad', 'hexa', 'tandem'];

function clampNum(v, min, fallback) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(n, min);
}

// Coerce/validate an incoming profile into the canonical shared schema.
export function normalizeProfile(raw) {
  const topology = String(raw.topology ?? 'quad').toLowerCase();
  return {
    id: raw.id || 'prof_' + Math.random().toString(36).slice(2, 12),
    name: String(raw.name ?? 'Untitled Drone').trim() || 'Untitled Drone',
    topology: VALID_TOPOLOGIES.includes(topology) ? topology : 'quad',
    mass: clampNum(raw.mass, 0.05, 0.6),
    arm_length: clampNum(raw.arm_length, 0.02, 0.11),
    max_thrust: clampNum(raw.max_thrust, 0.1, 7.0),
    drag_coeff: clampNum(raw.drag_coeff, 0.0, 0.1),
    updated_at: new Date().toISOString(),
  };
}

export function readProfiles() {
  try {
    if (!fs.existsSync(PROFILES_PATH)) return [];
    const raw = fs.readFileSync(PROFILES_PATH, 'utf-8');
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function writeAll(profiles) {
  const dir = path.dirname(PROFILES_PATH);
  const tmp = path.join(dir, `.drone_profiles.${process.pid}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(profiles, null, 2), 'utf-8');
  fs.renameSync(tmp, PROFILES_PATH); // atomic replace
}

// Create or update (upsert by id). Returns the saved profile.
export function upsertProfile(raw) {
  const profile = normalizeProfile(raw);
  const profiles = readProfiles();
  const idx = profiles.findIndex((p) => p.id === profile.id);
  if (idx >= 0) profiles[idx] = profile;
  else profiles.push(profile);
  writeAll(profiles);
  return profile;
}

// Delete by id. Returns true if something was removed.
export function deleteProfile(id) {
  const profiles = readProfiles();
  const next = profiles.filter((p) => p.id !== id);
  const removed = next.length !== profiles.length;
  if (removed) writeAll(next);
  return removed;
}

export { PROFILES_PATH };
