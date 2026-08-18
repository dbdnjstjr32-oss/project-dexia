// C2 command API — the HUD POSTs deploy/remove/arm actions here.
//
// AIP write-funnel: this route PROXIES to the FastAPI control plane
// (/api/sim/*), which is the single governed write path — clearance check,
// ActionBus ontology guards (LOST drone / kill-chain MAC), and the immutable
// SQLite lineage trail all live there. The browser never holds the API key;
// the key is attached server-side here.
//
// There is intentionally NO direct-write fallback: if the control plane is
// unreachable we return 503 rather than enqueueing an ungoverned command to
// commands.json (that bypassed clearance + ActionBus governance + lineage).
//
//   POST /api/command  { action:'spawn',  x, y, z?, lon, lat, profile? }
//   POST /api/command  { action:'remove', agent_id }
//   POST /api/command  { action:'arm'|'disarm'|'clear' }
//   GET  /api/command  -> { pending: [...] }   (debug: peek the queue, read-only)

import { readQueue } from '../../lib/commandStore';

const SIM_API = process.env.SIM_API_URL || 'http://127.0.0.1:8000';
// Key attached server-side. The built-in 'dexia-commander' only works when the
// FastAPI process runs in dev mode (DEXIA_ALLOW_DEFAULT_KEYS=1); a real backend
// fails it closed. Set SIM_API_KEY to your configured commander key in prod.
const SIM_API_KEY = process.env.SIM_API_KEY || 'dexia-commander';

// HUD command action -> governed FastAPI endpoint + body.
function toSimCall(body) {
  const action = String(body.action || '').toLowerCase();
  switch (action) {
    case 'spawn':
    case 'deploy':
      return { path: '/api/sim/deploy', body: { x: body.x, y: body.y, z: typeof body.z === 'number' ? body.z : 0.3, profile: body.profile || null, role: body.role === 'recon' ? 'recon' : 'kami', route: body.route || null } };
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
    // The governed FastAPI control plane is the ONLY write path. If it is
    // unreachable we do NOT fall back to an ungoverned commands.json write —
    // that bypassed clearance, the ActionBus guards, and the lineage trail.
    // Surface it as a retryable 503 so the operator knows governance is down.
    return res.status(503).json({
      error: 'CONTROL_PLANE_UNREACHABLE',
      detail: `governed control plane (${SIM_API}) unreachable: ${String(e)}`,
    });
  }
}
