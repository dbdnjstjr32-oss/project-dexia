/**
 * POST /api/wargame/run
 *
 * Spawns `python -m dexia.agent.loop_cli --scenario <id>` in the repo root,
 * waits for it to finish (up to 60 s), then reads and returns the generated
 * reasoning_trace.jsonl + world_snapshot.jsonl so the HUD can load them
 * in a single round-trip.
 *
 * Body: { scenario: "ridge-los-p4" }
 *
 * On Windows the virtual environment python is at .venv312/Scripts/python.exe;
 * fall back to the system `python` / `python3` if that isn't found.
 */

const { execFile } = require('child_process');
const fs   = require('fs');
const path = require('path');
const { REPO_ROOT, TRACE_PATH, SNAPSHOT_PATH } = require('../../../lib/wargamePaths');

const TIMEOUT_MS = 90_000; // 90 s — some scenarios take a while

function findPython() {
  const candidates = [
    path.join(REPO_ROOT, '.venv312', 'Scripts', 'python.exe'),
    path.join(REPO_ROOT, '.venv312', 'bin', 'python'),
    'python',
    'python3',
  ];
  for (const c of candidates) {
    if (!c.includes(path.sep)) return c; // bare command — let PATH resolve it
    if (fs.existsSync(c)) return c;
  }
  return 'python';
}

function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs.readFileSync(filePath, 'utf8')
    .split('\n')
    .filter(l => l.trim())
    .map(l => JSON.parse(l));
}

export default function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { scenario } = req.body || {};
  if (!scenario || typeof scenario !== 'string') {
    return res.status(400).json({ error: 'scenario 필드가 필요합니다.' });
  }
  // Basic sanitise — allow alphanumeric, hyphens, underscores only
  if (!/^[\w-]+$/.test(scenario)) {
    return res.status(400).json({ error: '유효하지 않은 시나리오 ID' });
  }

  const python = findPython();
  const args   = ['-m', 'dexia.agent.loop_cli', '--scenario', scenario];

  return new Promise((resolve) => {
    execFile(python, args, {
      cwd: REPO_ROOT,
      timeout: TIMEOUT_MS,
      env: { ...process.env },
    }, (err, stdout, stderr) => {
      if (err) {
        console.error('[run.js] loop_cli failed:', stderr || err.message);
        resolve(res.status(500).json({
          error: '시뮬레이션 실행 실패',
          detail: (stderr || err.message || '').slice(0, 800),
        }));
        return;
      }

      try {
        const cycles    = readJsonl(TRACE_PATH);
        const snapshots = readJsonl(SNAPSHOT_PATH);
        res.setHeader('Cache-Control', 'no-store');
        resolve(res.status(200).json({
          scenario,
          cycles,
          snapshots,
          stdout: stdout.slice(0, 1000),
        }));
      } catch (e) {
        resolve(res.status(500).json({ error: '결과 파일 읽기 실패', detail: String(e) }));
      }
    });
  });
}
