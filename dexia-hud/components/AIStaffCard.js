// AI Staff — Human-in-the-Loop tactical advisory card (AIP Sprint 1).
//
// Flow:  [⚡ AI 전술 분석] -> /api/ai/assess (FastAPI -> local Ollama agent)
//        -> shows the LLM assessment + recommended actions (COA)
//        -> commander clicks [승인] -> maps the recommendation to a sim command
//           and POSTs /api/command (queue -> streamer -> physics) ;  [거절] dismisses.
//
// The model NEVER executes on its own — execution is gated by this approval.

import { useState } from 'react';

// COA recommendation (tool) -> concrete sim command for /api/command.
function recToCommand(rec) {
  const t = rec.tool;
  const k = rec.kwargs || {};
  if (t === 'deploy_drone') return { action: 'spawn', x: k.x, y: k.y, z: 0.3 };
  if (t === 'recall_drone') return { action: 'remove', agent_id: k.agent_id };
  if (t === 'activate') return { action: 'arm' };
  if (t === 'standby') return { action: 'disarm' };
  return null;
}

function recLabel(rec) {
  const t = rec.tool;
  const k = rec.kwargs || {};
  if (t === 'deploy_drone') return `드론 전개 → [${(+k.x).toFixed?.(1) ?? k.x}, ${(+k.y).toFixed?.(1) ?? k.y}]`;
  if (t === 'recall_drone') return `드론 회수 → ${k.agent_id}`;
  if (t === 'activate') return '교전 활성화 (ACTIVATE)';
  if (t === 'standby') return '대기 / 교전 중지 (STANDBY)';
  return `${t}(${JSON.stringify(k)})`;
}

export default function AIStaffCard() {
  const [busy, setBusy] = useState(false);
  const [coa, setCoa] = useState(null);
  const [err, setErr] = useState(null);
  const [done, setDone] = useState({}); // idx -> 'approved'|'rejected'

  async function requestAssessment() {
    setBusy(true);
    setErr(null);
    setCoa(null);
    setDone({});
    try {
      const r = await fetch('/api/ai/assess', { method: 'POST' });
      const j = await r.json();
      if (!j.ok) setErr(j.error || j.detail || 'assessment failed');
      else setCoa(j);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approve(rec, idx) {
    const cmd = recToCommand(rec);
    if (!cmd) {
      setDone((d) => ({ ...d, [idx]: 'rejected' }));
      return;
    }
    try {
      await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cmd),
      });
      setDone((d) => ({ ...d, [idx]: 'approved' }));
    } catch {
      setErr('실행 명령 전송 실패');
    }
  }

  return (
    <div style={S.card}>
      <div style={S.head}>
        <span style={S.title}>AI STAFF · 실 LLM 결재</span>
        <button style={S.runBtn} disabled={busy} onClick={requestAssessment}>
          {busy ? '분석 중…' : '⚡ AI 전술 분석'}
        </button>
      </div>

      {err && <div style={S.err}>⚠ {err}</div>}

      {coa && (
        <>
          {coa.model && (
            <div style={S.model}>
              model: {coa.model} · blocks: {(coa.blocks || []).join(' → ')}
            </div>
          )}
          {coa.comms && (
            <div style={S.comms}>
              네트워크 생존율 {Math.round((coa.comms.network_survivability ?? 1) * 100)}%
              {coa.comms.degraded?.length ? ` · 링크저하: ${coa.comms.degraded.join(', ')}` : ''}
            </div>
          )}
          <div style={S.assessment}>{coa.assessment || '(평가 텍스트 없음)'}</div>
          {coa.killchain_decision && (
            <div style={S.killchain}>킬체인 결심: {coa.killchain_decision}</div>
          )}

          <div style={S.recTitle}>제안 행동방침 (COA)</div>
          {(coa.recommendations || []).length === 0 && (
            <div style={S.dim}>권고 액션 없음 — 현상 유지.</div>
          )}
          {(coa.recommendations || []).map((rec, i) => (
            <div key={i} style={S.recRow}>
              <span style={S.recText}>
                {recLabel(rec)}
                {rec.governance === 'rejected' && (
                  <span style={S.gov} title={rec.governance_reason}> ⛔ 거버넌스 거부</span>
                )}
                {rec.route && rec.route.length > 2 && (
                  <span style={S.route}> · 우회경로 {rec.route.length}점</span>
                )}
              </span>
              {done[i] ? (
                <span style={{ ...S.badge, color: done[i] === 'approved' ? '#52c41a' : '#ff7875' }}>
                  {done[i] === 'approved' ? '✓ 승인·실행' : '✕ 거절'}
                </span>
              ) : (
                <span style={S.btns}>
                  <button style={S.approve} onClick={() => approve(rec, i)}>승인</button>
                  <button style={S.reject} onClick={() => setDone((d) => ({ ...d, [i]: 'rejected' }))}>거절</button>
                </span>
              )}
            </div>
          ))}
        </>
      )}
      {!coa && !err && <div style={S.dim}>버튼을 눌러 현재 전장에 대한 AI 참모 분석을 요청하세요.</div>}
    </div>
  );
}

const S = {
  card: { background: 'rgba(13,18,24,0.82)', border: '1px solid #243042', borderRadius: 10, padding: 14, marginBottom: 12 },
  head: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #1f2933' },
  title: { fontSize: 11, letterSpacing: 1.5, color: '#7cc4ff', textTransform: 'uppercase', fontWeight: 800 },
  runBtn: { background: '#2f6da3', color: '#fff', border: 'none', borderRadius: 5, padding: '5px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer' },
  model: { fontSize: 9, color: '#5b6675', marginBottom: 4 },
  comms: { fontSize: 10.5, color: '#9fd0ff', marginBottom: 6 },
  killchain: { fontSize: 11.5, color: '#ffd7a8', background: 'rgba(40,28,12,0.5)', borderRadius: 4, padding: '6px 9px', marginTop: 6 },
  gov: { color: '#ff7875', fontSize: 10, fontWeight: 700 },
  route: { color: '#7cffb2', fontSize: 10 },
  assessment: { fontSize: 12.5, lineHeight: 1.5, color: '#dbe6f2', background: 'rgba(8,12,17,0.7)', borderLeft: '3px solid #40a9ff', borderRadius: 4, padding: '8px 10px', whiteSpace: 'pre-wrap', maxHeight: 180, overflowY: 'auto' },
  recTitle: { fontSize: 10.5, letterSpacing: 1, color: '#6b7785', margin: '12px 0 6px', textTransform: 'uppercase' },
  recRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, background: 'rgba(8,12,17,0.7)', border: '1px solid #161d25', borderRadius: 5, padding: '7px 9px', marginBottom: 6 },
  recText: { fontSize: 12, color: '#cfe3ff', fontWeight: 600 },
  btns: { display: 'flex', gap: 5 },
  approve: { background: '#1f7a3d', color: '#fff', border: 'none', borderRadius: 4, padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer' },
  reject: { background: 'transparent', color: '#ff7875', border: '1px solid #5a2a30', borderRadius: 4, padding: '4px 10px', fontSize: 11, cursor: 'pointer' },
  badge: { fontSize: 11, fontWeight: 800 },
  err: { fontSize: 11, color: '#ff9a9a', background: 'rgba(42,18,21,0.6)', borderRadius: 4, padding: '6px 8px', marginBottom: 8 },
  dim: { fontSize: 11.5, color: '#6b7785', padding: '4px 0' },
};
