// C2 command API — the HUD POSTs deploy/remove/arm actions here.
//
// AIP write-funnel: this route now PROXIES to the FastAPI control plane
// (/api/sim/*), which is the single governed write path — clearance check,
// ActionBus ontology guards (LOST drone / kill-chain MAC), and the immutable
// SQLite lineage trail all live there. The browser never holds the API key;
// the key is attached server-side here.
//
// If the FastAPI control plane is unreachable (dev without uvicorn running), we
// fall back to the legacy direct commands.json enqueue so the local demo still
// works — but that path is ungoverned and clearly a dev fallback.
//
//   POST /api/command  { action:'spawn',  x, y, z?, lon, lat, profile? }
//   POST /api/command  { action:'remove', agent_id }
//   POST /api/command  { action:'arm'|'disarm'|'clear' }
//   GET  /api/command  -> { pending: [...] }   (debug: peek the legacy queue)

import { enqueueCommand, readQueue } from '../../lib/commandStore';

const SIM_API = process.env.SIM_API_URL || 'http://127.0.0.1:8000';
const SIM_API_KEY = process.env.SIM_API_KEY || 'dexia-commander';

// HUD command action -> governed FastAPI endpoint + body.
function toSimCall(body) {
  const action = String(body.action || '').toLowerCase();
  switch (action) {
    case 'spawn':
    case 'deploy':
      return { path: '/api/sim/deploy', body: { x: body.x, y: body.y, z: typeof body.z === 'number' ? body.z : 0.3, profile: body.profile || null, role: body.role === 'recon' ? 'recon' : 'kami' } };
    case 'remove':
    case 'delete':
      return { path: '/api/sim/recall', body: { agent_id: body.agent_id } };
    case 'arm':
      return { path: '/api/sim/activate', body: {} };
    case 'disarm':
      return { path: '/api/sim/standby', body: {} };
    case 'clear':
      return { path: '/api/sim/clear', body: {} };
    case 'set_enemy':
      return { path: '/api/sim/enemy', body: { x: body.x, y: body.y } };
    case 'set_friendly':
      return { path: '/api/sim/friendly', body: { x: body.x, y: body.y } };
    default:
      return null;
  }
}

export default async function handler(req, res) {
  if (req.method === 'GET') {
    return res.status(200).json({ pending: readQueue() });
  }
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'GET, POST');
    return res.status(405).json({ error: 'METHOD_NOT_ALLOWED' });
  }

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
  const call = toSimCall(body);
  if (!call) return res.status(400).json({ error: 'UNKNOWN_ACTION' });

  // Primary path: governed FastAPI control plane.
  try {
    const r = await fetch(`${SIM_API}${call.path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dexia-Key': SIM_API_KEY },
      body: JSON.stringify(call.body),
    });
    const j = await r.json().catch(() => ({}));
    // Surface governance verdicts to the HUD verbatim (202 ok / 401 / 403 / 409).
    return res.status(r.status).json(j);
  } catch (e) {
    // Fallback: FastAPI down -> legacy direct enqueue (ungoverned, dev only).
    try {
      const action = String(body.action || '').toLowerCase();
      let stored;
      if (action === 'spawn' || action === 'deploy') {
        if (typeof body.x !== 'number' || typeof body.y !== 'number') {
          return res.status(400).json({ error: 'MISSING_COORDS' });
        }
        stored = enqueueCommand({ action: 'spawn', x: body.x, y: body.y, z: typeof body.z === 'number' ? body.z : 1.5, lon: body.lon, lat: body.lat, profile: body.profile || null });
      } else if (action === 'remove' || action === 'delete') {
        if (!body.agent_id) return res.status(400).json({ error: 'MISSING_AGENT_ID' });
        stored = enqueueCommand({ action: 'remove', agent_id: body.agent_id });
      } else {
        stored = enqueueCommand({ action });
      }
      return res.status(202).json({ queued: stored, governed: false, warning: `control plane unreachable (${SIM_API}); used ungoverned dev fallback` });
    } catch (err) {
      return res.status(503).json({ error: 'CONTROL_PLANE_UNREACHABLE', detail: String(e) });
    }
  }
}
