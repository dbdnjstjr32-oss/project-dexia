// Battlefield Table storage layer — reads/writes `battlefield_tables.json` at
// the project root (one dir up from /dexia-hud). A "battlefield table" is a
// saved, named workspace: a pre-designated map area (center + extent) plus the
// remembered enemy / friendly positions laid on it. Opening a table later
// restores those positions to the live sim.
//
// Mirrors lib/profileStore.js (Drone Garage): pure fs + JSON, thin API route.

import fs from 'fs';
import path from 'path';

const TABLES_PATH =
  process.env.BATTLEFIELD_TABLES_PATH || path.join(process.cwd(), '..', 'battlefield_tables.json');

function num(v, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}
function clamp(v, min, max, fallback) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(n, min), max);
}
// [x, y] local sim metres, or null.
function pos2(v) {
  if (!Array.isArray(v) || v.length < 2) return null;
  const x = Number(v[0]); const y = Number(v[1]);
  return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
}

// Coerce/validate an incoming table into the canonical schema.
export function normalizeTable(raw) {
  return {
    id: raw.id || 'bf_' + Math.random().toString(36).slice(2, 12),
    name: String(raw.name ?? 'Untitled Table').trim() || 'Untitled Table',
    // pre-designated area: center in local sim metres + half-size extent (sim m)
    center: pos2(raw.center) || [0, 0],
    extent_m: clamp(raw.extent_m, 5, 80, 20),
    // remembered unit layout (local sim metres) — null until placed
    enemy: pos2(raw.enemy),
    friendly: pos2(raw.friendly),
    updated_at: new Date().toISOString(),
  };
}

export function readTables() {
  try {
    if (!fs.existsSync(TABLES_PATH)) return [];
    const data = JSON.parse(fs.readFileSync(TABLES_PATH, 'utf-8'));
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function writeAll(tables) {
  const dir = path.dirname(TABLES_PATH);
  const tmp = path.join(dir, `.battlefield_tables.${process.pid}.tmp`);
  fs.writeFileSync(tmp, JSON.stringify(tables, null, 2), 'utf-8');
  fs.renameSync(tmp, TABLES_PATH); // atomic replace
}

// Create or update (upsert by id). Returns the saved table.
export function upsertTable(raw) {
  const table = normalizeTable(raw);
  const tables = readTables();
  const idx = tables.findIndex((t) => t.id === table.id);
  if (idx >= 0) tables[idx] = table;
  else tables.push(table);
  writeAll(tables);
  return table;
}

// Delete by id. Returns true if removed.
export function deleteTable(id) {
  const tables = readTables();
  const next = tables.filter((t) => t.id !== id);
  const removed = next.length !== tables.length;
  if (removed) writeAll(next);
  return removed;
}

export { TABLES_PATH };
