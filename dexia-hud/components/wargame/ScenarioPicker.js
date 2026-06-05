// ScenarioPicker — left-rail panel to browse and launch wargame scenarios.
//
// Fetches GET /api/wargame/scenarios → grouped by theater.
// Selecting a scenario POSTs /api/wargame/run and streams back cycles + snapshots.
//
// Shows:
//   • theater group headers
//   • each scenario: id, intent badge, terrain badge (3D/2D), force counts
//   • loading spinner + status message during run
//
// Props:
//   onRunComplete  fn({ cycles, snapshots, theater }) — called on success
//   selectedId     string — currently active scenario id

import { useState, useEffect, useCallback } from 'react';

const INTENT_COLOR = {
  destroy: '#ef4444', deny: '#f59e0b', delay: '#f97316',
  recon: '#3b82f6', seize: '#22c55e',
};
const THEATER_LABEL = {
  eastern_europe: '🌍 동유럽', ukraine: '🌍 우크라이나', ua_east: '🌍 우크라이나 동부',
  korea: '🇰🇷 한국', default: '🌐 기타',
};

function theaterLabel(t) {
  const key = (t || '').toLowerCase().replace(/-/g, '_');
  return THEATER_LABEL[key] || `🌐 ${t || '기타'}`;
}

export default function ScenarioPicker({ onRunComplete, selectedId }) {
  const [scenarios, setScenarios]   = useState([]);
  const [loading, setLoading]       = useState(false);
  const [runningId, setRunningId]   = useState(null);
  const [status, setStatus]         = useState('');
  const [error, setError]           = useState('');
  const [filter, setFilter]         = useState('');

  // Fetch scenario list on mount
  useEffect(() => {
    fetch('/api/wargame/scenarios')
      .then(r => r.json())
      .then(j => setScenarios(j.scenarios || []))
      .catch(e => setError('시나리오 목록 로드 실패: ' + e.message));
  }, []);

  const runScenario = useCallback(async (scenario) => {
    setLoading(true);
    setRunningId(scenario.id);
    setStatus(`${scenario.id} 실행 중…`);
    setError('');

    try {
      const r = await fetch('/api/wargame/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario: scenario.id }),
      });
      const j = await r.json();
      if (!r.ok) {
        setError(j.error || `HTTP ${r.status}`);
        setStatus('');
        return;
      }
      setStatus(`완료 — ${j.cycles?.length ?? 0} 사이클`);
      onRunComplete?.({
        cycles: j.cycles || [],
        snapshots: j.snapshots || [],
        theater: scenario.theater,
        hasTerrain: scenario.hasTerrain,
        scenarioId: scenario.id,
      });
    } catch (e) {
      setError('실행 오류: ' + e.message);
      setStatus('');
    } finally {
      setLoading(false);
      setRunningId(null);
    }
  }, [onRunComplete]);

  // Group by theater
  const filtered = scenarios.filter(s =>
    !filter || s.id?.toLowerCase().includes(filter.toLowerCase()) ||
    s.theater?.toLowerCase().includes(filter.toLowerCase())
  );
  const groups = {};
  for (const s of filtered) {
    const t = s.theater || 'default';
    (groups[t] = groups[t] || []).push(s);
  }
  // Hand-crafted scenarios always first
  const handcrafted = filtered.filter(s =>
    ['ridge-los-p4','ridge-assault-3d','ua-east-armor-thrust-007'].includes(s.id)
  );
  const generated = filtered.filter(s =>
    !['ridge-los-p4','ridge-assault-3d','ua-east-armor-thrust-007'].includes(s.id)
  );

  return (
    <div style={S.root}>
      <div style={S.header}>
        <span style={S.title}>📋 시나리오</span>
        <span style={S.count}>{scenarios.length}개</span>
      </div>

      {/* Search filter */}
      <div style={{ padding: '6px 10px' }}>
        <input
          type="text"
          placeholder="검색…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={S.filterInput}
        />
      </div>

      {/* Status / error */}
      {status && (
        <div style={{ ...S.statusBar, color: '#22c55e' }}>
          {loading && <span style={S.spinner}>⟳ </span>}
          {status}
        </div>
      )}
      {error && <div style={{ ...S.statusBar, color: '#ef4444' }}>⚠ {error}</div>}

      <div style={S.list}>
        {/* Hand-crafted scenarios */}
        {handcrafted.length > 0 && (
          <>
            <div style={S.groupHeader}>⭐ 검증 시나리오</div>
            {handcrafted.map(s => (
              <ScenarioRow
                key={s.id} scenario={s}
                isActive={s.id === selectedId}
                isRunning={s.id === runningId && loading}
                disabled={loading}
                onClick={() => runScenario(s)}
              />
            ))}
          </>
        )}

        {/* Generated scenarios grouped by theater */}
        {Object.entries(groups)
          .filter(([, rs]) => rs.some(s => !['ridge-los-p4','ridge-assault-3d','ua-east-armor-thrust-007'].includes(s.id)))
          .map(([theater, rows]) => {
            const genRows = rows.filter(s => !['ridge-los-p4','ridge-assault-3d','ua-east-armor-thrust-007'].includes(s.id));
            if (!genRows.length) return null;
            return (
              <div key={theater}>
                <div style={S.groupHeader}>{theaterLabel(theater)}</div>
                {genRows.map(s => (
                  <ScenarioRow
                    key={s.id} scenario={s}
                    isActive={s.id === selectedId}
                    isRunning={s.id === runningId && loading}
                    disabled={loading}
                    onClick={() => runScenario(s)}
                  />
                ))}
              </div>
            );
          })
        }

        {filtered.length === 0 && !error && (
          <div style={{ color: '#4a5568', fontSize: 12, padding: '20px 10px', textAlign: 'center' }}>
            {filter ? '검색 결과 없음' : '시나리오를 불러오는 중…'}
          </div>
        )}
      </div>
    </div>
  );
}

function ScenarioRow({ scenario, isActive, isRunning, disabled, onClick }) {
  const s = scenario;
  const intentC = INTENT_COLOR[s.intent] || '#8899aa';
  const bg = isActive ? 'rgba(30,58,100,0.3)' : 'rgba(13,18,24,0.5)';
  const border = isActive ? '#3b82f6' : '#1a2130';

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        width: '100%', textAlign: 'left',
        background: isRunning ? 'rgba(30,58,100,0.5)' : bg,
        border: `1px solid ${border}`,
        borderRadius: 6, padding: '7px 10px', marginBottom: 4,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled && !isRunning ? 0.6 : 1,
        transition: 'all 0.15s',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: '#cfe3ff', letterSpacing: 0.3 }}>
          {isRunning ? '⟳ ' : ''}{s.id}
        </span>
        <div style={{ display: 'flex', gap: 4 }}>
          {s.hasTerrain && (
            <span style={{ ...S.badge, background: '#1a3a2a', borderColor: '#22c55e', color: '#86efac' }}>
              3D
            </span>
          )}
          {s.intent && (
            <span style={{ ...S.badge, background: `${intentC}22`, borderColor: intentC, color: intentC }}>
              {s.intent}
            </span>
          )}
        </div>
      </div>
      <div style={{ fontSize: 10, color: '#6b7785', marginTop: 3, display: 'flex', gap: 8 }}>
        {s.blueCount > 0 && <span>🔵 {s.blueCount}</span>}
        {s.redCount  > 0 && <span>🔴 {s.redCount}</span>}
        {s.theater && <span style={{ color: '#4a5568' }}>{s.theater}</span>}
      </div>
    </button>
  );
}

const S = {
  root: {
    display: 'flex', flexDirection: 'column', height: '100%',
    background: 'rgba(9,13,18,0.92)', backdropFilter: 'blur(8px)',
    border: '1px solid #1f2933', borderRadius: 10,
    boxShadow: '0 8px 30px rgba(0,0,0,0.5)',
    overflow: 'hidden',
  },
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '10px 12px 4px',
    borderBottom: '1px solid #1a2130',
  },
  title: { fontSize: 12, fontWeight: 800, color: '#7cc4ff', letterSpacing: 1 },
  count: { fontSize: 10, color: '#4a5568' },
  filterInput: {
    width: '100%', background: '#0d1117', border: '1px solid #2a3441',
    borderRadius: 5, padding: '5px 8px', fontSize: 11,
    color: '#cfe3ff', outline: 'none',
  },
  statusBar: {
    padding: '4px 12px', fontSize: 11, fontWeight: 600,
  },
  spinner: { display: 'inline-block', animation: 'wg-spin 1s linear infinite' },
  list: { flex: 1, overflowY: 'auto', padding: '4px 8px 8px' },
  groupHeader: {
    fontSize: 10, fontWeight: 700, color: '#4a5568',
    letterSpacing: 1, padding: '8px 2px 4px',
    textTransform: 'uppercase',
    borderBottom: '1px solid #1a2130', marginBottom: 4,
  },
  badge: {
    fontSize: 9, fontWeight: 700, letterSpacing: 0.3,
    padding: '1px 5px', borderRadius: 3, border: '1px solid',
  },
};
