// HITL Commander Approval API (AIP Phase 8.7).
//
// The AI files doctrine changes into `pending_proposals.json` instead of
// overwriting `recipes.json` directly. This route surfaces that queue and gates
// it behind a human:
//
//   GET  /api/proposals                 -> { count, proposals: [...] }
//   POST /api/proposals {id, action}    -> action: 'approve' | 'reject'
//        approve: apply the proposed rule change to recipes.json (version +0.1)
//                 and remove it from the queue. The env (Phase 8.6) loads the
//                 new recipes.json on reset, cascading the rule to the physics.
//        reject : drop the proposal from the queue, recipes.json untouched.
//
// File locations default to the repo's dexia/aip/* (the HUD dev-server cwd is
// /dexia-hud, so '..' is the repo root); override with env vars if needed.

import fs from 'fs';
import path from 'path';

const AIP_DIR = process.env.AIP_DIR || path.join(process.cwd(), '..', 'dexia', 'aip');
const RECIPES_PATH = process.env.RECIPES_PATH || path.join(AIP_DIR, 'recipes.json');
const PENDING_PATH = process.env.PENDING_PROPOSALS_PATH || path.join(AIP_DIR, 'pending_proposals.json');

function readJson(p, fallback) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(p, obj) {
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2) + '\n', 'utf-8');
  fs.renameSync(tmp, p);
}

function readPending() {
  const data = readJson(PENDING_PATH, []);
  return Array.isArray(data) ? data : [];
}

export default function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'GET') {
    const proposals = readPending().filter((p) => p && p.status !== 'RESOLVED');
    return res.status(200).json({ count: proposals.length, proposals });
  }

  if (req.method === 'POST') {
    const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
    const action = body.action;
    if (action !== 'approve' && action !== 'reject') {
      return res.status(400).json({ ok: false, error: "action must be 'approve' or 'reject'" });
    }

    const queue = readPending();
    // operate on the given id, or the head of the queue if none supplied
    const idx = body.id ? queue.findIndex((p) => p.id === body.id) : 0;
    if (idx < 0 || queue.length === 0) {
      return res.status(404).json({ ok: false, error: 'proposal not found' });
    }
    const proposal = queue[idx];

    if (action === 'reject') {
      queue.splice(idx, 1);
      writeJsonAtomic(PENDING_PATH, queue);
      return res.status(200).json({ ok: true, action: 'reject', id: proposal.id });
    }

    // approve -> apply the proposed update to recipes.json
    const recipe = readJson(RECIPES_PATH, null);
    if (!recipe || typeof recipe !== 'object') {
      return res.status(500).json({ ok: false, error: 'recipes.json missing or invalid' });
    }
    recipe.rules = recipe.rules || {};
    const applied = {};
    for (const [rule, value] of Object.entries(proposal.proposed_update || {})) {
      if (rule in recipe.rules) {
        recipe.rules[rule] = value; // whitelist: only known rules
        applied[rule] = value;
      }
    }
    const prev = Number(recipe.version || 1.0);
    recipe.version = Math.round((prev + 0.1) * 10) / 10;
    writeJsonAtomic(RECIPES_PATH, recipe);

    queue.splice(idx, 1); // remove the approved proposal from the queue
    writeJsonAtomic(PENDING_PATH, queue);

    return res.status(200).json({
      ok: true,
      action: 'approve',
      id: proposal.id,
      applied,
      from_version: prev,
      to_version: recipe.version,
      recipe,
    });
  }

  res.setHeader('Allow', 'GET, POST');
  return res.status(405).json({ ok: false, error: 'METHOD_NOT_ALLOWED' });
}
