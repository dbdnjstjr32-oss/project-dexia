/**
 * GET /api/wargame/snapshot
 *
 * Reads world_snapshot.jsonl from the repo root and returns the parsed array
 * of WorldSnapshot objects (one per decision cycle, 1:1 with trace cycles).
 *
 * Each snapshot shape (from loop_cli.py SnapshotRunner._emit):
 *   { cycle, tick, has_terrain,
 *     blue[]: [{id, cls, pos:[x,y,z], category, alive}],
 *     red[]:  [{id, cls, pos:[x,y,z], category, alive}],
 *     los[]:  [{observer, target, visible, blocked_by_terrain}] }
 *
 * Returns 404 if the file doesn't exist (trace was generated without loop_cli).
 */

const fs = require('fs');
const { SNAPSHOT_PATH } = require('../../../lib/wargamePaths');

export default function handler(req, res) {
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  if (!fs.existsSync(SNAPSHOT_PATH)) {
    return res.status(404).json({
      error: 'world_snapshot.jsonl 없음',
      hint: 'loop_cli.py로 시나리오를 실행하면 자동 생성됩니다.',
    });
  }

  try {
    const lines = fs.readFileSync(SNAPSHOT_PATH, 'utf8')
      .split('\n')
      .filter(l => l.trim());
    const snapshots = lines.map(l => JSON.parse(l));
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json({ snapshots });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
}
