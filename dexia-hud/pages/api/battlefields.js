// Battlefield Table CRUD API — reads/writes the shared battlefield_tables.json.
//
//   GET    /api/battlefields            -> { tables: [...] }
//   POST   /api/battlefields  (table)   -> { table }   (create; id auto-assigned)
//   PUT    /api/battlefields  (table)   -> { table }   (update by id)
//   DELETE /api/battlefields?id=...     -> { ok: true }

import { readTables, upsertTable, deleteTable } from '../../lib/battlefieldStore';

export default function handler(req, res) {
  try {
    switch (req.method) {
      case 'GET':
        return res.status(200).json({ tables: readTables() });

      case 'POST': {
        const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
        const created = upsertTable({ ...body, id: undefined }); // force new id
        return res.status(201).json({ table: created });
      }

      case 'PUT': {
        const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body || {};
        if (!body.id) return res.status(400).json({ error: 'MISSING_ID' });
        const saved = upsertTable(body);
        return res.status(200).json({ table: saved });
      }

      case 'DELETE': {
        const id = req.query.id || (req.body && req.body.id);
        if (!id) return res.status(400).json({ error: 'MISSING_ID' });
        const removed = deleteTable(id);
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
