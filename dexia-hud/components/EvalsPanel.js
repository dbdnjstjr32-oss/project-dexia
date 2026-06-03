// Evals + Observability panel (AIP Phase 9).
//
// Renders the EpisodeEvalSuite scorecard (six thresholded metrics + PASS/FAIL
// verdict) and the three immutable audit-trail summaries (action / llm /
// ontology). [▶ 평가 실행] scores the CURRENT mission from live telemetry; on
// mount it shows the last stored report (or the live audit block if none yet).

import { useState, useEffect, useCallback } from 'react';

const fmt = (v, unit) =>
  v === null || v === undefined ? 'n/a' : `${v}${unit ? ' ' + unit : ''}`;

function MetricRow({ m }) {
  const state = !m.evaluated ? 'na' : m.passed ? 'pass' : 'fail';
  const color = state === 'pass' ? '#52c41a' : state === 'fail' ? '#ff7875' : '#6b7785';
  const op = m.direction === 'max' ? '≥' : '≤';
  return (
    <div style={S.row}>
      <span style={{ ...S.dot, background: color }} />
      <span style={S.rowLabel}>{m.label}</span>
      <span style={{ ...S.rowVal, color }}>{fmt(m.value, m.unit)}</span>
      <span style={S.rowThr}>
        {op} {m.threshold}
      </span>
    </div>
  );
}

function AuditLine({ k, children }) {
  return (
    <div style={S.auditLine}>
      <span style={S.auditKey}>{k}</span>
      <span style={S.auditVal}>{children}</span>
    </div>
  );
}

export default function EvalsPanel() {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(async (method) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch('/api/evals', { method });
      const j = await r.json();
      if (j.error) setErr(j.error);
      else setReport(j);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load('GET');
  }, [load]);

  const obs = report?.observability;
  const verdict = report?.verdict;
  const vColor = verdict === 'PASS' ? '#52c41a' : verdict === 'FAIL' ? '#ff7875' : '#6b7785';

  return (
    <div style={S.card}>
      <div style={S.head}>
        <span style={S.title}>EVALS · 관찰가능성</span>
        <button style={S.runBtn} disabled={busy} onClick={() => load('POST')}>
          {busy ? '평가 중…' : '▶ 평가 실행'}
        </button>
      </div>

      {err && <div style={S.err}>⚠ {err}</div>}

      {report && (
        <>
          <div style={S.verdictRow}>
            <span style={{ ...S.verdict, color: vColor, borderColor: vColor }}>
              {verdict || 'NO RUN'}
            </span>
            {report.evaluated > 0 && (
              <span style={S.verdictSub}>
                {report.passed}/{report.evaluated} 통과
                {report.scenario ? ` · ${report.scenario}` : ''}
              </span>
            )}
          </div>

          {(report.metrics || []).length > 0 && (
            <div style={S.metrics}>
              {report.metrics.map((m) => (
                <MetricRow key={m.name} m={m} />
              ))}
            </div>
          )}
          {(report.metrics || []).length === 0 && (
            <div style={S.dim}>아직 채점된 에피소드가 없습니다 — [평가 실행]을 누르세요.</div>
          )}

          {obs && (
            <>
              <div style={S.auditTitle}>감사 트레일 (불변)</div>
              <AuditLine k="action_audit">
                {obs.action.submitted}건 · 승인율 {pct(obs.action.accept_rate)}
                {obs.action.rejected ? ` · 거부 ${obs.action.rejected}` : ''}
              </AuditLine>
              <AuditLine k="llm_audit">
                {obs.llm.calls}콜 · 정상율 {pct(obs.llm.ok_rate)}
                {obs.llm.mean_latency_ms ? ` · ${Math.round(obs.llm.mean_latency_ms)}ms` : ''}
              </AuditLine>
              <AuditLine k="ontology_state">
                {Object.entries(obs.ontology.objects || {})
                  .map(([t, n]) => `${t.replace('Object', '')} ${n}`)
                  .join(' · ') || '비어 있음'}
                {obs.ontology.links ? ` · 링크 ${obs.ontology.links}` : ''}
              </AuditLine>
            </>
          )}
        </>
      )}
      {!report && !err && <div style={S.dim}>평가 트레일 로딩 중…</div>}
    </div>
  );
}

const pct = (v) => (v === null || v === undefined ? 'n/a' : `${Math.round(v * 100)}%`);

const S = {
  card: { background: 'rgba(13,18,24,0.82)', border: '1px solid #243042', borderRadius: 10, padding: 14, marginTop: 12 },
  head: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, paddingBottom: 6, borderBottom: '1px solid #1f2933' },
  title: { fontSize: 11, letterSpacing: 1.5, color: '#7cc4ff', textTransform: 'uppercase', fontWeight: 800 },
  runBtn: { background: '#2f6da3', color: '#fff', border: 'none', borderRadius: 5, padding: '5px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer' },
  verdictRow: { display: 'flex', alignItems: 'center', gap: 9, margin: '4px 0 10px' },
  verdict: { fontSize: 12, fontWeight: 800, letterSpacing: 1, border: '1px solid', borderRadius: 4, padding: '3px 9px' },
  verdictSub: { fontSize: 11, color: '#8c97a3' },
  metrics: { display: 'flex', flexDirection: 'column', gap: 5 },
  row: { display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(8,12,17,0.7)', border: '1px solid #161d25', borderRadius: 5, padding: '6px 9px' },
  dot: { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  rowLabel: { fontSize: 11.5, color: '#cfe3ff', flex: 1, fontWeight: 600 },
  rowVal: { fontSize: 12, fontWeight: 700, fontFamily: 'Consolas, monospace', minWidth: 52, textAlign: 'right' },
  rowThr: { fontSize: 10, color: '#5b6675', minWidth: 46, textAlign: 'right' },
  auditTitle: { fontSize: 10.5, letterSpacing: 1, color: '#6b7785', margin: '12px 0 6px', textTransform: 'uppercase' },
  auditLine: { display: 'flex', gap: 8, fontSize: 11, padding: '3px 0', borderBottom: '1px solid rgba(31,41,51,0.5)' },
  auditKey: { color: '#5b6675', fontFamily: 'Consolas, monospace', minWidth: 96 },
  auditVal: { color: '#aebfd0', flex: 1 },
  err: { fontSize: 11, color: '#ff9a9a', background: 'rgba(42,18,21,0.6)', borderRadius: 4, padding: '6px 8px', marginBottom: 8 },
  dim: { fontSize: 11.5, color: '#6b7785', padding: '4px 0' },
};
