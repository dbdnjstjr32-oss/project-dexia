// ReasoningTimeline — the centrepiece of the wargame HUD.
//
// Renders the per-cycle AI decision record as a vertical scrollable timeline
// on the right rail.  Clicking a cycle card sets the shared cycleIndex (which
// drives the map layers).  A play button steps through cycles automatically.
//
// Each cycle card shows:
//   • cycle index + tick number + mission intent
//   • Decision chips coloured by kind:
//       collect  → #3b82f6 (blue)  — ISR tasking
//       suppress → #f59e0b (amber) — EW jamming
//       fires    → #ef4444 (red)   — artillery
//       strike   → #8b5cf6 (violet) — loiter munition
//   • reasoning string (Korean AI rationale)
//   • gaps[] — tracks too uncertain to act on
//   • governance: accepted / rejected verdict per decision
//   • events summary: what actually happened this cycle

import { useEffect, useRef, useCallback } from 'react';

const KIND_COLOR = {
  collect:  { bg: '#1d3461', border: '#3b82f6', text: '#93c5fd', label: 'COLLECT'  },
  suppress: { bg: '#3d2b05', border: '#f59e0b', text: '#fcd34d', label: 'SUPPRESS' },
  fires:    { bg: '#3d1010', border: '#ef4444', text: '#fca5a5', label: 'FIRES'    },
  strike:   { bg: '#2d1b52', border: '#8b5cf6', text: '#c4b5fd', label: 'STRIKE'   },
};

const INTENT_COLOR = {
  destroy: '#ef4444', deny: '#f59e0b', delay: '#f97316',
  recon: '#3b82f6', seize: '#22c55e',
};

function KindChip({ kind, asset, trackId }) {
  const c = KIND_COLOR[kind] || KIND_COLOR.fires;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 7px', borderRadius: 4,
      background: c.bg, border: `1px solid ${c.border}`,
      color: c.text, fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
      marginRight: 4, marginBottom: 4, whiteSpace: 'nowrap',
    }}>
      {c.label}
      {asset && <span style={{ color: '#8899aa', fontWeight: 400 }}>·{asset}</span>}
    </span>
  );
}

function GovBadge({ status }) {
  const ok = status === 'accepted';
  return (
    <span style={{
      padding: '1px 5px', borderRadius: 3, fontSize: 9, fontWeight: 700,
      background: ok ? '#14532d' : '#4c1d1d',
      border: `1px solid ${ok ? '#22c55e' : '#ef4444'}`,
      color: ok ? '#86efac' : '#fca5a5',
      letterSpacing: 0.3,
    }}>
      {ok ? 'ACC' : 'REJ'}
    </span>
  );
}

function EventBadge({ ev }) {
  const iconMap = { fire: '💥', strike: '🎯', jam: '📡', isr: '🛰' };
  const icon = iconMap[ev.action] || '⚡';
  const statusColor = ev.status === 'impact' || ev.status === 'suppressed' || ev.status === 'tasked'
    ? '#22c55e' : '#f59e0b';
  return (
    <span style={{
      display: 'inline-flex', gap: 3, alignItems: 'center',
      padding: '1px 6px', borderRadius: 3, marginRight: 3, marginTop: 2,
      background: '#0d1117', border: '1px solid #2a3441',
      fontSize: 10, color: statusColor,
    }}>
      {icon} {ev.status}
      {ev.killed?.length > 0 && <span style={{ color: '#ef4444' }}> ✕{ev.killed.length}</span>}
    </span>
  );
}

function CycleCard({ cycle, isActive, onClick }) {
  const hasFires   = cycle.decisions?.some(d => d.kind === 'fires'   || d.kind === 'strike');
  const hasSuppress= cycle.decisions?.some(d => d.kind === 'suppress');
  const hasCollect = cycle.decisions?.some(d => d.kind === 'collect');
  const events     = (cycle.events || []).filter(e => e.status !== 'rejected');
  const intentC    = INTENT_COLOR[cycle.intent] || '#8899aa';

  const borderColor = isActive ? '#3b82f6' : '#1f2933';
  const bgColor     = isActive ? 'rgba(30, 58, 100, 0.35)' : 'rgba(13,18,24,0.7)';

  return (
    <div
      onClick={onClick}
      style={{
        background: bgColor,
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: '10px 12px',
        cursor: 'pointer',
        transition: 'border-color 0.15s, background 0.15s',
        boxShadow: isActive ? `0 0 12px ${borderColor}44` : 'none',
      }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            width: 22, height: 22, borderRadius: '50%',
            background: isActive ? '#3b82f6' : '#1f2933',
            border: `1px solid ${isActive ? '#3b82f6' : '#374151'}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 10, fontWeight: 800, color: isActive ? '#fff' : '#6b7785',
            flexShrink: 0,
          }}>
            {cycle.cycle}
          </span>
          <span style={{ fontSize: 10, color: '#6b7785', letterSpacing: 0.5 }}>
            T{cycle.tick}
          </span>
          <span style={{
            fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
            color: intentC, textTransform: 'uppercase',
          }}>
            {cycle.intent}
          </span>
        </div>
        <div style={{ fontSize: 10, color: '#4a5568' }}>
          {cycle.perceive?.tracks ?? 0} tracks
        </div>
      </div>

      {/* Decision chips */}
      {cycle.decisions?.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', marginBottom: 6 }}>
          {cycle.decisions.map((d, i) => (
            <KindChip key={i} kind={d.kind} asset={d.asset} trackId={d.track_id} />
          ))}
        </div>
      )}

      {/* Governance badges inline with decisions */}
      {cycle.governance?.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
          {cycle.governance.map((g, i) => (
            <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#6b7785' }}>
              {g.cmd}
              <GovBadge status={g.status} />
            </span>
          ))}
        </div>
      )}

      {/* AI reasoning text */}
      {cycle.reasoning && (
        <div style={{
          fontSize: 11, color: '#9fb2c6', lineHeight: 1.5,
          marginBottom: 6, padding: '4px 8px',
          background: 'rgba(8,12,17,0.6)', borderRadius: 4,
          borderLeft: '2px solid #2a3441',
          wordBreak: 'keep-all',
        }}>
          {cycle.reasoning}
        </div>
      )}

      {/* Gaps — uncertain tracks */}
      {cycle.gaps?.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          {cycle.gaps.map((g, i) => (
            <div key={i} style={{
              fontSize: 10, color: '#f59e0b', marginBottom: 2,
              display: 'flex', alignItems: 'center', gap: 4,
            }}>
              <span style={{ color: '#6b7785' }}>⚠</span>
              <span style={{ color: '#8899aa' }}>{g.track}</span>
              <span>conf {Math.round(g.conf * 100)}%</span>
              <span style={{ color: '#6b7785' }}>— {g.why}</span>
            </div>
          ))}
        </div>
      )}

      {/* Events summary */}
      {events.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap' }}>
          {events.map((ev, i) => <EventBadge key={i} ev={ev} />)}
        </div>
      )}

      {/* No decisions */}
      {!cycle.decisions?.length && (
        <div style={{ fontSize: 11, color: '#4a5568', fontStyle: 'italic' }}>
          관측 지속 — 결정 없음
        </div>
      )}
    </div>
  );
}

export default function ReasoningTimeline({
  cycles, cycleIndex, onCycleSelect, playing, onTogglePlay,
}) {
  const listRef     = useRef(null);
  const activeRef   = useRef(null);

  // Auto-scroll active card into view
  useEffect(() => {
    if (activeRef.current) {
      activeRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [cycleIndex]);

  if (!cycles?.length) {
    return (
      <div style={S.empty}>
        <div style={S.emptyIcon}>🎯</div>
        <div style={S.emptyText}>시나리오를 선택하고<br />Run을 눌러 재생을 시작하세요</div>
      </div>
    );
  }

  const total = cycles.length;

  return (
    <div style={S.root}>
      {/* Controls */}
      <div style={S.controls}>
        <button onClick={onTogglePlay} style={{ ...S.playBtn, ...(playing ? S.playBtnActive : {}) }}>
          {playing ? '⏸ PAUSE' : '▶ PLAY'}
        </button>
        <span style={S.counter}>
          {cycleIndex + 1} / {total}
        </span>
        <button
          onClick={() => onCycleSelect(0)}
          style={S.navBtn}
          title="처음으로"
        >⏮</button>
        <button
          onClick={() => onCycleSelect(Math.max(0, cycleIndex - 1))}
          style={S.navBtn}
          title="이전 사이클"
        >◀</button>
        <button
          onClick={() => onCycleSelect(Math.min(total - 1, cycleIndex + 1))}
          style={S.navBtn}
          title="다음 사이클"
        >▶</button>
        <button
          onClick={() => onCycleSelect(total - 1)}
          style={S.navBtn}
          title="마지막으로"
        >⏭</button>
      </div>

      {/* Scrub slider */}
      <div style={{ padding: '0 12px 8px' }}>
        <input
          type="range" min={0} max={total - 1} value={cycleIndex}
          onChange={e => onCycleSelect(Number(e.target.value))}
          style={{ width: '100%', accentColor: '#3b82f6', cursor: 'pointer' }}
        />
      </div>

      {/* Kill-chain legend */}
      <div style={S.legend}>
        {Object.entries(KIND_COLOR).map(([kind, c]) => (
          <span key={kind} style={{ ...S.legendChip, borderColor: c.border, color: c.text, background: c.bg }}>
            {c.label}
          </span>
        ))}
      </div>

      {/* Cycle cards */}
      <div ref={listRef} style={S.list}>
        {cycles.map((c, i) => (
          <div key={c.cycle ?? i} ref={i === cycleIndex ? activeRef : null}>
            <CycleCard
              cycle={c}
              isActive={i === cycleIndex}
              onClick={() => onCycleSelect(i)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

const S = {
  root: {
    display: 'flex', flexDirection: 'column', height: '100%',
    background: 'rgba(9,13,18,0.92)', backdropFilter: 'blur(8px)',
    border: '1px solid #1f2933', borderRadius: 10,
    boxShadow: '0 8px 30px rgba(0,0,0,0.6)',
    overflow: 'hidden',
  },
  controls: {
    display: 'flex', alignItems: 'center', gap: 6,
    padding: '10px 12px 6px',
    borderBottom: '1px solid #1a2130',
  },
  playBtn: {
    padding: '5px 12px', borderRadius: 5,
    background: '#1d3461', border: '1px solid #3b82f6',
    color: '#93c5fd', cursor: 'pointer', fontSize: 11, fontWeight: 800,
    letterSpacing: 0.5, transition: 'all 0.15s',
  },
  playBtnActive: {
    background: '#3b82f6', color: '#fff',
    boxShadow: '0 0 10px rgba(59,130,246,0.5)',
  },
  counter: {
    fontSize: 11, color: '#6b7785', fontVariantNumeric: 'tabular-nums',
    flex: 1, textAlign: 'center',
  },
  navBtn: {
    padding: '4px 8px', borderRadius: 4,
    background: 'transparent', border: '1px solid #2a3441',
    color: '#8899aa', cursor: 'pointer', fontSize: 12,
  },
  legend: {
    display: 'flex', gap: 4, padding: '4px 12px 8px', flexWrap: 'wrap',
  },
  legendChip: {
    fontSize: 9, fontWeight: 700, letterSpacing: 0.5,
    padding: '2px 6px', borderRadius: 3, border: '1px solid',
  },
  list: {
    flex: 1, overflowY: 'auto', padding: '0 8px 8px',
    display: 'flex', flexDirection: 'column', gap: 6,
    scrollBehavior: 'smooth',
  },
  empty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', height: '100%',
    background: 'rgba(9,13,18,0.92)', backdropFilter: 'blur(8px)',
    border: '1px solid #1f2933', borderRadius: 10,
  },
  emptyIcon: { fontSize: 40, marginBottom: 16 },
  emptyText: {
    color: '#4a5568', fontSize: 13, textAlign: 'center', lineHeight: 1.6,
  },
};
