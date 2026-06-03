// C2 command API — the HUD POSTs deploy/remove actions here; they are queued to
// commands.json for the Python backend to drain.
//
//   POST /api/command  { action:'spawn',  x, y, z?, lon, lat, profile? }
//   POST /api/command  { action:'remove', agent_id }
//   GET  /api/command  -> { pending: [...] }   (debug: peek the queue)

import { enqueueCommand, readQueue } from '../../lib/commandStore';

export default function handler(req, res) {
  try {
    if (req.method === 'GET') {
      return res.status(200).json({ pending: readQueue() });
    }
    if (req.method === 'POST') {
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
      const action = String(body.action || '').toLowerCase();

      if (action === 'spawn' || action === 'deploy') {
        if (typeof body.x !== 'number' || typeof body.y !== 'number') {
          return res.status(400).json({ error: 'MISSING_COORDS' });
        }
        const stored = enqueueCommand({
          action: 'spawn',
          x: body.x, y: body.y, z: typeof body.z === 'number' ? body.z : 1.5,
          lon: body.lon, lat: body.lat,
          profile: body.profile || null,
        });
        return res.status(202).json({ queued: stored });
      }

      if (action === 'remove' || action === 'delete') {
        if (!body.agent_id) return res.status(400).json({ error: 'MISSING_AGENT_ID' });
        const stored = enqueueCommand({ action: 'remove', agent_id: body.agent_id });
        return res.status(202).json({ queued: stored });
      }

      return res.status(400).json({ error: 'UNKNOWN_ACTION' });
    }

    res.setHeader('Allow', 'GET, POST');
    return res.status(405).json({ error: 'METHOD_NOT_ALLOWED' });
  } catch (err) {
    return res.status(500).json({ error: 'INTERNAL_SERVER_ERROR', details: String(err) });
  }
}
