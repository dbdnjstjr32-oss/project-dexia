/**
 * GET /api/wargame/scenarios
 *
 * Scans scenarios/ and scenarios/generated/ for *.yaml files and returns
 * a lightweight summary of each (id, theater, intent, terrain flag, unit counts).
 * Used by ScenarioPicker to list available missions grouped by theater.
 *
 * Self-contained — reads files directly; no FastAPI server required.
 */

const fs   = require('fs');
const path = require('path');
const { SCENARIOS_DIR } = require('../../../lib/wargamePaths');

/** Very small YAML parser — just enough to pull top-level scalar fields
 *  from a Dexia scenario file without a full yaml library dependency. */
function parseScenarioYaml(text) {
  const get = (key) => {
    // Handles "  key: value" at any indent
    const m = text.match(new RegExp(`^\\s*${key}:\\s*(.+)$`, 'm'));
    return m ? m[1].trim().replace(/['"]/g, '') : null;
  };
  const hasTerrain = /^\s*terrain\s*:/m.test(text);
  const redMatches  = [...text.matchAll(/^\s*-\s*\{cls:[^}]*\bn:\s*(\d+)/gm)];
  const blueSection = text.match(/blue:([\s\S]*?)(?:red:|feeds:|$)/);
  const redSection  = text.match(/red:([\s\S]*?)(?:feeds:|$)/);
  const countUnits = (section) => {
    if (!section) return 0;
    return [...section[1].matchAll(/\bn:\s*(\d+)/g)]
      .reduce((s, m) => s + parseInt(m[1], 10), 0);
  };
  return {
    id:         get('id'),
    theater:    get('theater'),
    intent:     get('intent'),
    hasTerrain,
    blueCount: countUnits(blueSection),
    redCount:  countUnits(redSection),
  };
}

function scanDir(dir) {
  try {
    return fs.readdirSync(dir)
      .filter(f => f.endsWith('.yaml'))
      .map(f => path.join(dir, f));
  } catch {
    return [];
  }
}

export default function handler(req, res) {
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  const files = [
    ...scanDir(SCENARIOS_DIR),
    ...scanDir(path.join(SCENARIOS_DIR, 'generated')),
  ];

  const scenarios = [];
  for (const fp of files) {
    try {
      const text = fs.readFileSync(fp, 'utf8');
      const s = parseScenarioYaml(text);
      if (s.id) scenarios.push({ ...s, file: path.basename(fp) });
    } catch {
      // skip unreadable files
    }
  }

  // Sort: hand-crafted scenarios first, then generated; within each group by id
  scenarios.sort((a, b) => {
    const aGen = a.file.includes('generated') || a.id?.match(/\d{3}$/);
    const bGen = b.file.includes('generated') || b.id?.match(/\d{3}$/);
    if (aGen !== bGen) return aGen ? 1 : -1;
    return (a.id || '').localeCompare(b.id || '');
  });

  res.setHeader('Cache-Control', 'no-store');
  return res.status(200).json({ scenarios });
}
