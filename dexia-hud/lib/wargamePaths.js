/**
 * wargamePaths — centralised repo-root path resolution for the wargame HUD.
 *
 * The Next.js dev server runs from `dexia-hud/`; the data files (JSONL traces,
 * scenario YAMLs) live one level up in the repo root.  All wargame API routes
 * import from here so the path logic lives in one place.
 *
 * Node.js (server-side) only — never imported by browser bundles.
 */

const path = require('path');

// dexia-hud/ → repo root (one level up from Next.js cwd)
const REPO_ROOT = path.join(process.cwd(), '..');

const TRACE_PATH     = path.join(REPO_ROOT, 'reasoning_trace.jsonl');
const SNAPSHOT_PATH  = path.join(REPO_ROOT, 'world_snapshot.jsonl');
const EVALS_PATH     = path.join(REPO_ROOT, 'scenario_evals.jsonl');
const SCENARIOS_DIR  = path.join(REPO_ROOT, 'scenarios');

module.exports = { REPO_ROOT, TRACE_PATH, SNAPSHOT_PATH, EVALS_PATH, SCENARIOS_DIR };
