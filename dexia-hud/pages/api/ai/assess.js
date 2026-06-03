// HITL proxy — forwards the HUD's "AI 분석" request to the Python control API
// (FastAPI on :8000), which runs the local Ollama tactical agent and returns a
// Course of Action. Server-side fetch avoids CORS; the browser only talks to Next.

const SIM_API = process.env.SIM_API_URL || 'http://127.0.0.1:8000';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ ok: false, error: 'METHOD_NOT_ALLOWED' });
  }
  try {
    const r = await fetch(`${SIM_API}/api/sim/assess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const j = await r.json();
    return res.status(r.status).json(j);
  } catch (e) {
    return res.status(503).json({
      ok: false,
      error: `AI 제어 서버 미연결 (${SIM_API}). 실행: .venv312\\Scripts\\python.exe -m uvicorn dexia.api.sim_api:app --port 8000`,
      detail: String(e),
    });
  }
}
