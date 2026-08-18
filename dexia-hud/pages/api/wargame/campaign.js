/**
 * GET /api/wargame/campaign
 *
 * Reads scenario_evals.jsonl from the repo root and returns the parsed array
 * of MissionResult objects plus pre-computed aggregates and by-theater rollup.
 *
 * Each result shape (from MissionResult asdict in dexia/agent/campaign.py):
 *   { scenario, theater, intent, outcome, score, neutralised, red_total,
 *     blue_lost, cycles, decisions_by_kind{}, governance_accept_rate }
 *
 * Returns 404 if the file doesn't exist (run the campaign first).
 */

const fs = require('fs');
const { EVALS_PATH } = require('../../../lib/wargamePaths');

const PASS_SCORE = 0.7;

function aggregates(results) {
  const n = results.length || 1;
  const outcomes = {};
  results.forEach(r => { outcomes[r.outcome] = (outcomes[r.outcome] || 0) + 1; });
  return {
    scenarios: results.length,
    pass_rate: round(results.filter(r => r.score >= PASS_SCORE).length / n),
    mean_score: round(results.reduce((s, r) => s + r.score, 0) / n),
    mean_neutralised_frac: round(
      results.reduce((s, r) => s + r.neutralised / Math.max(1, r.red_total), 0) / n
    ),
    mean_blue_lost: round(results.reduce((s, r) => s + r.blue_lost, 0) / n),
    outcomes,
  };
}

function byTheater(results) {
  const groups = {};
  results.forEach(r => {
    (groups[r.theater] = groups[r.theater] || []).push(r);
  });
  return Object.fromEntries(
    Object.entries(groups).sort().map(([t, rs]) => [t, {
      scenarios: rs.length,
      pass_rate: round(rs.filter(r => r.score >= PASS_SCORE).length / rs.length),
      mean_score: round(rs.reduce((s, r) => s + r.score, 0) / rs.length),
    }])
  );
}

function round(v) { return Math.round(v * 1000) / 1000; }

export default function handler(req, res) {
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  if (!fs.existsSync(EVALS_PATH)) {
    return res.status(404).json({
      error: 'scenario_evals.jsonl 없음',
      hint: '실행: python -m dexia.agent.campaign --count 20',
    });
  }

  try {
    const lines = fs.readFileSync(EVALS_PATH, 'utf8')
      .split('\n')
      .filter(l => l.trim());
    const results = lines.map(l => JSON.parse(l));
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json({
      results,
      aggregates: aggregates(results),
      by_theater: byTheater(results),
    });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
}
