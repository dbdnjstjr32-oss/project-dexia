// CampaignScoreboard — bottom section of the left rail.
//
// Fetches GET /api/wargame/campaign and renders:
//   • KPI cards: pass rate, mean score, mean neutralised, mean blue lost
//   • by-theater table: theater | n | pass% | score
//   • outcomes breakdown: bar chart of success_destroyed / fail_breach / fail_blue_loss / in_progress
//
// Design goal: match the numbers printed by `python -m dexia.agent.campaign`.

import { useState, useEffect } from 'react';

const OUTCOME_COLOR = {
  success_destroyed: { color: '#22c55e', label: '✓ 섬멸 성공',   bg: '#14532d' },
  fail_breach:       { color: '#ef4444', label: '✕ 전선 돌파',   bg: '#4c1d1d' },
  fail_blue_loss:    { color: '#f97316', label: '✕ 아군 손실',   bg: '#431407' },
  in_progress:       { color: '#6b7785', label: '○ 진행 중',     bg: '#1f2937' },
};

function KpiCard({ label, value, color, unit }) {
  return (
    <div style={{
      flex: 1, minWidth: 70,
      background: 'rgba(13,18,24,0.7)', border: '1px solid #1f2933',
      borderRadius: 7, padding: '8px 10px', textAlign: 'center',
    }}>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || '#cfe3ff', fontVariantNumeric: 'tabular-nums' }}>
        {value}{unit || ''}
      </div>
      <div style={{ fontSize: 9, color: '#4a5568', letterSpacing: 0.5, marginTop: 2 }}>
        {label}
      </div>
    </div>
  );
}

function OutcomeBar({ outcomes, total }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: '#4a5568', letterSpacing: 0.5, marginBottom: 6 }}>
        결과 분포
      </div>
      {Object.entries(OUTCOME_COLOR).map(([key, c]) => {
        const n = outcomes?.[key] || 0;
        const pct = total > 0 ? Math.round((n / total) * 100) : 0;
        if (n === 0) return null;
        return (
          <div key={key} style={{ marginBottom: 5 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
              <span style={{ fontSize: 10, color: c.color }}>{c.label}</span>
              <span style={{ fontSize: 10, color: '#6b7785' }}>{n} ({pct}%)</span>
            </div>
            <div style={{ height: 5, background: '#1f2933', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{
                width: `${pct}%`, height: '100%',
                background: c.color, borderRadius: 3,
                transition: 'width 0.4s ease',
              }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function CampaignScoreboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/wargame/campaign')
      .then(r => r.json())
      .then(j => {
        if (j.error) setError(j.hint || j.error);
        else setData(j);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div style={S.root}>
      <div style={S.header}><span style={S.title}>📊 캠페인 스코어보드</span></div>
      <div style={{ padding: 16, color: '#4a5568', fontSize: 12, textAlign: 'center' }}>
        불러오는 중…
      </div>
    </div>
  );

  if (error || !data) return (
    <div style={S.root}>
      <div style={S.header}><span style={S.title}>📊 캠페인 스코어보드</span></div>
      <div style={{ padding: 12, fontSize: 11, color: '#f59e0b' }}>
        {error || '데이터 없음'}
        <div style={{ color: '#4a5568', marginTop: 4, fontSize: 10 }}>
          python -m dexia.agent.campaign --count 20
        </div>
      </div>
    </div>
  );

  const { aggregates: agg, by_theater, results } = data;
  const total = agg?.scenarios || 0;

  return (
    <div style={S.root}>
      <div style={S.header}>
        <span style={S.title}>📊 캠페인 스코어보드</span>
        <span style={{ fontSize: 10, color: '#4a5568' }}>{total}개 시나리오</span>
      </div>

      <div style={{ padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto', flex: 1 }}>
        {/* KPI cards */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <KpiCard
            label="통과율"
            value={`${Math.round((agg.pass_rate || 0) * 100)}`}
            unit="%"
            color={agg.pass_rate >= 0.9 ? '#22c55e' : agg.pass_rate >= 0.7 ? '#f59e0b' : '#ef4444'}
          />
          <KpiCard
            label="평균 점수"
            value={(agg.mean_score || 0).toFixed(2)}
            color="#7cc4ff"
          />
          <KpiCard
            label="무력화율"
            value={`${Math.round((agg.mean_neutralised_frac || 0) * 100)}`}
            unit="%"
            color="#22c55e"
          />
          <KpiCard
            label="아군 손실"
            value={(agg.mean_blue_lost || 0).toFixed(1)}
            color={agg.mean_blue_lost < 0.5 ? '#22c55e' : '#f59e0b'}
          />
        </div>

        {/* Outcomes bar chart */}
        <OutcomeBar outcomes={agg.outcomes} total={total} />

        {/* By-theater table */}
        {by_theater && Object.keys(by_theater).length > 0 && (
          <div>
            <div style={{ fontSize: 9, color: '#4a5568', letterSpacing: 0.5, marginBottom: 6 }}>
              극장별 현황
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
              <thead>
                <tr style={{ color: '#4a5568', borderBottom: '1px solid #1f2933' }}>
                  <th style={{ textAlign: 'left', padding: '3px 4px', fontWeight: 600 }}>극장</th>
                  <th style={{ textAlign: 'right', padding: '3px 4px' }}>n</th>
                  <th style={{ textAlign: 'right', padding: '3px 4px' }}>통과</th>
                  <th style={{ textAlign: 'right', padding: '3px 4px' }}>점수</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(by_theater).map(([t, m]) => (
                  <tr key={t} style={{ borderBottom: '1px solid #111820', color: '#9fb2c6' }}>
                    <td style={{ padding: '4px 4px', maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {t}
                    </td>
                    <td style={{ textAlign: 'right', padding: '4px 4px', color: '#6b7785' }}>
                      {m.scenarios}
                    </td>
                    <td style={{ textAlign: 'right', padding: '4px 4px', color: m.pass_rate >= 0.9 ? '#22c55e' : '#f59e0b' }}>
                      {Math.round(m.pass_rate * 100)}%
                    </td>
                    <td style={{ textAlign: 'right', padding: '4px 4px', color: '#7cc4ff' }}>
                      {m.mean_score.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

const S = {
  root: {
    display: 'flex', flexDirection: 'column',
    background: 'rgba(9,13,18,0.92)', backdropFilter: 'blur(8px)',
    border: '1px solid #1f2933', borderRadius: 10,
    boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
    overflow: 'hidden',
  },
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '10px 12px 6px',
    borderBottom: '1px solid #1a2130',
  },
  title: { fontSize: 12, fontWeight: 800, color: '#7cc4ff', letterSpacing: 1 },
};
