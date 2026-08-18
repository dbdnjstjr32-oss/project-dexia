// Drone Garage CRUD API — reads/writes the shared drone_profiles.json.
//
//   GET    /api/profiles            -> { profiles: [...] }
//   POST   /api/profiles  (profile) -> { profile }   (create; id auto-assigned)
//   PUT    /api/profiles  (profile) -> { profile }   (update by id)
//   DELETE /api/profiles?id=...     -> { ok: true }

import { readProfiles, upsertProfile, deleteProfile } from '../../lib/profileStore';

export default function handler(req, res) {
  try {
    switch (req.method) {
      case 'GET':
        return res.status(200).json({ profiles: readProfiles() });

      case 'POST': {
        const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
        const created = upsertProfile({ ...body, id: undefined }); // force new id
        return res.status(201).json({ profile: created });
      }

      case 'PUT': {
        const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
        if (!body.id) return res.status(400).json({ error: 'MISSING_ID' });
        const saved = upsertProfile(body);
        return res.status(200).json({ profile: saved });
      }

      case 'DELETE': {
        const id = req.query.id || (req.body && req.body.id);
        if (!id) return res.status(400).json({ error: 'MISSING_ID' });
        const removed = deleteProfile(id);
        return res.status(200).json({ ok: removed });
      }

      default:
        res.setHeader('Allow', 'GET, POST, PUT, DELETE');
        return res.status(405).json({ error: 'METHOD_NOT_ALLOWED' });
    }
  } catch (err) {
    return res.status(500).json({ error: 'INTERNAL_SERVER_ERROR', details: String(err) });
  }
}
