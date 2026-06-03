// API route: serve the latest telemetry snapshot to the HUD.
//
// Reads the `telemetry.json` written by the Python streamer
// (../telemetry.json relative to the dev-server cwd, which is /dexia-hud).
// Override the location with the TELEMETRY_PATH env var if needed.
//
// (A Redis-backed variant would read the `dexia:telemetry` key instead; the
//  JSON-file path is the zero-setup default to match the backend's fallback.)

import fs from 'fs';
import path from 'path';

const TELEMETRY_PATH =
  process.env.TELEMETRY_PATH || path.join(process.cwd(), '..', 'telemetry.json');

export default function handler(req, res) {
  try {
    if (!fs.existsSync(TELEMETRY_PATH)) {
      return res.status(200).json({
        error: 'no-telemetry',
        message: `telemetry.json not found at ${TELEMETRY_PATH}. Start the streamer: .venv312\\Scripts\\python.exe telemetry_stream.py`,
      });
    }
    const raw = fs.readFileSync(TELEMETRY_PATH, 'utf-8');
    const data = JSON.parse(raw);
    // disable caching so the HUD always sees the freshest tick
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json(data);
  } catch (err) {
    // a transient parse error can happen if we read mid-write; report softly
    return res.status(200).json({ error: 'read-failed', message: String(err) });
  }
}
