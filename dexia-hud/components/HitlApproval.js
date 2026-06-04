// HITL Commander Approval (AIP Phase 8.7).
//
// Polls /api/proposals; when the AI Command Staff has filed a doctrine change,
// it raises a high-visibility modal showing the AI's chain-of-thought and the
// proposed rule diff (e.g. scatter 10 → 50). The doctrine CANNOT change the
// physics until the commander clicks [APPROVE 승인]; [REJECT 거절] drops it.
// APPROVE → /api/proposals writes recipes.json → the env (Phase 8.6) loads it
// on reset → the rule cascades to the MuJoCo physics.

import { useCallback, useEffect, useState } from 'react';

const POLL_MS = 3000;

export default function HitlApproval() {
  const [proposals, setProposals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [err, setErr] = useState(null);

  const poll = useCallback(async () => {
    try {
      const r = await fetch('/api/proposals');
      const j = await r.json();
      setProposals(j.proposals || []);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  const decide = useCallback(
    async (proposal, action) => {
      setBusy(true);
      try {
        const r = await fetch('/api/proposals', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: proposal.id, action }),
        });
        const j = await r.json();
        if (!j.ok) {
          setErr(j.error || 'request failed');
        } else if (action === 'approve') {
          setToast(`✓ 승인 — 교리 v${j.from_version} → v${j.to_version} 적용 (물리 반영)`);
        } else {
          setToast('✕ 거절 — 제안 폐기');
        }
        await poll();
        setTimeout(() => setToast(null), 4000);
      } catch (e) {
        setErr(String(e));
      } finally {
        setBusy(false);
      }
    },
    [poll]
  );

  const p = proposals[0];
  if (!p) {
    return toast ? <div style={S.toast}>{toast}</div> : null;
  }

  return (
    <div style={S.backdrop}>
      <div style={S.modal}>
        <div style={S.alert}>⚠️ AI COMMAND STAFF PROPOSAL · 지휘관 결재 요청</div>

        <div style={S.metaRow}>
          <span style={S.scenario}>{p.scenario || 'DOCTRINE'}</span>
          <span style={S.version}>
            교리 v{p.from_version} → <b style={{ color: '#ffd76b' }}>v{p.to_version}</b>
          </span>
          {p.trigger_event && <span style={S.trigger}>trigger: {p.trigger_event}</span>}
        </div>

        {p.root_cause && (
          <div style={S.section}>
            <div style={S.label}>근본 원인 (Root Cause)</div>
            <div style={S.rootCause}>{p.root_cause}</div>
          </div>
        )}

        {(p.chain_of_thought || []).length > 0 && (
          <div style={S.section}>
            <div style={S.label}>AI 추론 (Chain of Thought)</div>
            <ol style={S.cot}>
              {p.chain_of_thought.map((step, i) => (
                <li key={i} style={S.cotItem}>{step}</li>
              ))}
            </ol>
          </div>
        )}

        <div style={S.section}>
          <div style={S.label}>제안 변경 (Proposed Change)</div>
          {(p.changes || []).map((c, i) => (
            <div key={i} style={S.change}>
              <span style={S.rule}>{c.rule}</span>
              <span style={S.diff}>
                <span style={S.from}>{String(c.from)}</span>
                <span style={S.arrow}> → </span>
                <span style={S.to}>{String(c.to)}</span>
              </span>
            </div>
          ))}
        </div>

        {p.rationale && <div style={S.rationale}>“{p.rationale}”</div>}
        {err && <div style={S.err}>⚠ {err}</div>}

        <div style={S.btnRow}>
          <button style={{ ...S.btn, ...S.approve }} disabled={busy} onClick={() => decide(p, 'approve')}>
            ✔ APPROVE · 승인
          </button>
          <button style={{ ...S.btn, ...S.reject }} disabled={busy} onClick={() => decide(p, 'reject')}>
            ✕ REJECT · 거절
          </button>
        </div>
        {proposals.length > 1 && (
          <div style={S.queue}>+{proposals.length - 1} more pending proposal(s)</div>
        )}
      </div>
    </div>
  );
}

const S = {
  backdrop: {
    position: 'fixed', inset: 0, zIndex: 200, display: 'flex',
    alignItems: 'center', justifyContent: 'center',
    background: 'rgba(4,7,11,0.6)', backdropFilter: 'blur(3px)',
  },
  modal: {
    width: 560, maxWidth: '92vw', maxHeight: '88vh', overflowY: 'auto',
    background: 'linear-gradient(180deg,#141b24,#0d1218)',
    border: '1px solid #b8860b', borderRadius: 12, padding: 18,
    boxShadow: '0 0 0 1px rgba(255,193,7,0.25), 0 18px 60px rgba(0,0,0,0.7)',
    fontFamily: 'Segoe UI, system-ui, sans-serif', color: '#e6e6e6',
  },
  alert: {
    background: 'repeating-linear-gradient(45deg,#3a2a00,#3a2a00 10px,#241a00 10px,#241a00 20px)',
    color: '#ffd76b', fontWeight: 800, letterSpacing: 1, fontSize: 13.5,
    textAlign: 'center', padding: '9px 10px', borderRadius: 7, marginBottom: 14,
    border: '1px solid #6b5200',
  },
  metaRow: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 },
  scenario: { fontSize: 11, fontWeight: 800, letterSpacing: 1, color: '#7cc4ff', border: '1px solid #2f6da3', borderRadius: 4, padding: '2px 8px' },
  version: { fontSize: 13, color: '#cfe3ff' },
  trigger: { fontSize: 10.5, color: '#ff9a9a', fontFamily: 'Consolas, monospace' },
  section: { marginBottom: 12 },
  label: { fontSize: 10.5, letterSpacing: 1, color: '#6b7785', textTransform: 'uppercase', marginBottom: 5 },
  rootCause: { fontSize: 12.5, lineHeight: 1.5, color: '#ffd7a8', background: 'rgba(40,28,12,0.5)', borderLeft: '3px solid #b8860b', borderRadius: 4, padding: '8px 10px' },
  cot: { margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 4 },
  cotItem: { fontSize: 12.5, lineHeight: 1.45, color: '#dbe6f2' },
  change: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, background: 'rgba(8,12,17,0.7)', border: '1px solid #243042', borderRadius: 6, padding: '8px 12px', marginBottom: 6 },
  rule: { fontSize: 12.5, fontFamily: 'Consolas, monospace', color: '#cfe3ff', fontWeight: 600 },
  diff: { fontSize: 15, fontWeight: 800, fontFamily: 'Consolas, monospace' },
  from: { color: '#ff7875' },
  arrow: { color: '#6b7785' },
  to: { color: '#7cffb2' },
  rationale: { fontSize: 12, fontStyle: 'italic', color: '#9fb3c8', margin: '4px 0 12px', lineHeight: 1.5 },
  err: { fontSize: 11, color: '#ff9a9a', background: 'rgba(42,18,21,0.6)', borderRadius: 4, padding: '6px 8px', marginBottom: 10 },
  btnRow: { display: 'flex', gap: 12, marginTop: 6 },
  btn: { flex: 1, padding: '13px 0', fontSize: 14, fontWeight: 800, letterSpacing: 0.5, border: 'none', borderRadius: 8, cursor: 'pointer' },
  approve: { background: '#1f7a3d', color: '#fff', boxShadow: '0 0 16px rgba(31,122,61,0.5)' },
  reject: { background: 'transparent', color: '#ff7875', border: '1px solid #7a1f1f' },
  queue: { fontSize: 10.5, color: '#6b7785', textAlign: 'center', marginTop: 10 },
  toast: { position: 'fixed', bottom: 18, left: '50%', transform: 'translateX(-50%)', zIndex: 200, background: 'rgba(13,30,18,0.95)', color: '#7cffb2', border: '1px solid #1f7a3d', borderRadius: 8, padding: '10px 18px', fontSize: 12.5, fontWeight: 700, boxShadow: '0 8px 30px rgba(0,0,0,0.6)' },
};
