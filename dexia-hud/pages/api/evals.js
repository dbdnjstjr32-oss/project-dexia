// Evals proxy — forwards the HUD Evals panel to the Python control API
// (FastAPI on :8000), which owns the EpisodeEvalSuite + the three audit trails.
// Server-side fetch avoids CORS; the browser only talks to Next.
//
//   GET  /api/evals          -> /api/evals/latest  (last report or live audit)
//   POST /api/evals          -> /api/evals/run     (score the current mission)

const SIM_API = process.env.SIM_API_URL || 'http://127.0.0.1:8000';

export default async function handler(req, res) {
  const run = req.method === 'POST';
  const url = `${SIM_API}/api/evals/${run ? 'run' : 'latest'}`;
  try {
    const r = await fetch(url, {
      method: run ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
    });
    const j = await r.json();
    res.setHeader('Cache-Control', 'no-store');
    return res.status(r.status).json(j);
  } catch (e) {
    return res.status(503).json({
      error: `평가 서버 미연결 (${SIM_API}). 실행: .venv312\\Scripts\\python.exe -m uvicorn dexia.api.sim_api:app --port 8000`,
      detail: String(e),
    });
  }
}
