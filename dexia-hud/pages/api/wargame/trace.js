/**
 * GET /api/wargame/trace
 *
 * Reads reasoning_trace.jsonl from the repo root and returns the parsed array
 * of DecisionRecord objects (one per decision cycle).
 *
 * Each record shape (from DecisionRecord.to_dict in dexia/agent/loop.py):
 *   { cycle, tick, intent, perceive, fusion[], gaps[], asset_match{},
 *     reasoning, decisions[], governance[], events[] }
 *
 * Returns 404 if the file doesn't exist (run a scenario first).
 */

const fs = require('fs');
const { TRACE_PATH } = require('../../../lib/wargamePaths');

export default function handler(req, res) {
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  if (!fs.existsSync(TRACE_PATH)) {
    return res.status(404).json({
      error: '아직 실행된 시나리오가 없습니다.',
      hint: 'ScenarioPicker에서 시나리오를 선택하고 Run을 누르세요.',
    });
  }

  try {
    const lines = fs.readFileSync(TRACE_PATH, 'utf8')
      .split('\n')
      .filter(l => l.trim());
    const cycles = lines.map(l => JSON.parse(l));
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json({ cycles });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
}
