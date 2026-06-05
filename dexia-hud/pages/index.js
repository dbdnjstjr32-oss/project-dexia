import { memo, useState, useEffect, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { useTelemetry } from '../lib/useTelemetry';
import { lngLatToLocal, clampLocal } from '../lib/geo';
import DroneGarage from '../components/DroneGarage';
import AIStaffCard from '../components/AIStaffCard';
import EvalsPanel from '../components/EvalsPanel';
import HitlApproval from '../components/HitlApproval';
import BattlefieldTable from '../components/BattlefieldTable';

// MapLibre touches `window`; load the map client-side only.
const TacticalMap = dynamic(() => import('../components/TacticalMap'), { ssr: false });

const LEVEL_COLORS = {
  critical: '#ff4d4f',
  warning: '#faad14',
  success: '#52c41a',
  info: '#40a9ff',
};

export default function Dashboard() {
  // High-frequency poll; map updates are imperative so this only refreshes text.
  const { data: telemetry, error, connected } = useTelemetry(200);
  const [basemap, setBasemap] = useState('satellite');
  const [garageOpen, setGarageOpen] = useState(false);
  const [invert, setInvert] = useState(true); // tactical colour inversion (toggle)
  const [builderOpen, setBuilderOpen] = useState(false);
  const [buildTool, setBuildTool] = useState('drone'); // 'enemy'|'friendly'|'drone'|'remove'
  const [profiles, setProfiles] = useState([]);
  const [profileId, setProfileId] = useState('');
  const [droneRole, setDroneRole] = useState('recon'); // 'recon' (scout) | 'kami' (strike)
  const [cmdMsg, setCmdMsg] = useState(null);

  // Battlefield Tables — saved, named map areas remembering enemy/friendly layout
  const [bfOpen, setBfOpen] = useState(false);
  const [tables, setTables] = useState([]);
  const [activeTableId, setActiveTableId] = useState(null);
  const [picking, setPicking] = useState(false);   // map-click sets a new table centre
  const [pickDraft, setPickDraft] = useState(null); // {name, extent_m}

  const armed = !!telemetry?.armed;
  // 'bf_center' while picking a table centre; else the builder tool (if open).
  const activeTool = picking ? 'bf_center' : builderOpen ? buildTool : 'off';
  const activeTable = tables.find((t) => t.id === activeTableId) || null;

  // load airframe profiles for the DRONE tool when the builder opens
  useEffect(() => {
    if (!builderOpen) return;
    fetch('/api/profiles')
      .then((r) => r.json())
      .then((j) => {
        setProfiles(j.profiles || []);
        if (!profileId && j.profiles?.length) setProfileId(j.profiles[0].id);
      })
      .catch(() => {});
  }, [builderOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const sendCommand = useCallback(async (body, label) => {
    try {
      const r = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      // Governed funnel returns {ok, lineage_id, ...}; dev fallback returns
      // {queued, ...}; rejections return {detail:{reason}} (409) or {detail}/{error}.
      if (r.ok && j.ok !== false) {
        setCmdMsg(label || `${body.action} ok`);
      } else {
        const reason = (j.detail && (j.detail.reason || j.detail)) || j.error || `HTTP ${r.status}`;
        setCmdMsg('⛔ ' + String(reason));
      }
    } catch (e) {
      setCmdMsg('command error: ' + e);
    }
  }, []);

  // ---- Battlefield Table CRUD ----
  const refreshTables = useCallback(async () => {
    try {
      const j = await (await fetch('/api/battlefields')).json();
      setTables(j.tables || []);
    } catch { /* keep last */ }
  }, []);

  useEffect(() => { if (bfOpen) refreshTables(); }, [bfOpen, refreshTables]);

  const createTable = useCallback(async (table) => {
    try {
      const j = await (await fetch('/api/battlefields', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(table),
      })).json();
      if (j.table) { await refreshTables(); setActiveTableId(j.table.id); setCmdMsg(`전장 테이블 '${j.table.name}' 생성`); }
    } catch { setCmdMsg('테이블 생성 실패'); }
  }, [refreshTables]);

  // persist a field into the ACTIVE table (auto-remember placements)
  const saveActive = useCallback((patch) => {
    setTables((cur) => {
      const t = cur.find((x) => x.id === activeTableId);
      if (t) {
        fetch('/api/battlefields', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...t, ...patch }),
        }).then(() => refreshTables()).catch(() => {});
      }
      return cur;
    });
  }, [activeTableId, refreshTables]);

  const startPickCenter = useCallback((draft) => { setPickDraft(draft); setPicking(true); }, []);

  const openTable = useCallback((t) => {
    setActiveTableId(t.id);
    // restore the remembered layout to the LIVE sim (governed funnel)
    if (t.enemy) sendCommand({ action: 'set_enemy', x: t.enemy[0], y: t.enemy[1] }, `적 진지 복원`);
    if (t.friendly) sendCommand({ action: 'set_friendly', x: t.friendly[0], y: t.friendly[1] }, `아군 진영 복원`);
    setCmdMsg(`전장 '${t.name}' 로드`);
  }, [sendCommand]);

  const deleteTable = useCallback(async (id) => {
    try {
      await fetch('/api/battlefields?id=' + encodeURIComponent(id), { method: 'DELETE' });
      if (activeTableId === id) setActiveTableId(null);
      refreshTables();
    } catch { /* ignore */ }
  }, [activeTableId, refreshTables]);

  const onMapClick = useCallback(
    ({ lng, lat, tool }) => {
      // clamp into the active table's area (or the default arena) so units never
      // land kilometres off-screen
      const at_t = tables.find((t) => t.id === activeTableId);
      const [x, y] = clampLocal(
        lngLatToLocal(lng, lat),
        at_t ? at_t.center : [0, 0],
        at_t ? at_t.extent_m : undefined
      );
      const at = `[${x.toFixed(1)}, ${y.toFixed(1)}]`;
      if (tool === 'bf_center') {
        if (pickDraft) createTable({ ...pickDraft, center: clampLocal(lngLatToLocal(lng, lat)) });
        setPicking(false);
        return;
      }
      if (tool === 'enemy') {
        sendCommand({ action: 'set_enemy', x, y }, `적 진지 → ${at}`);
        if (activeTableId) saveActive({ enemy: [x, y] }); // remember into active table
      } else if (tool === 'friendly') {
        sendCommand({ action: 'set_friendly', x, y }, `아군 진영 → ${at}`);
        if (activeTableId) saveActive({ friendly: [x, y] });
      } else if (tool === 'drone') {
        const profile = profiles.find((p) => p.id === profileId) || null;
        const label = droneRole === 'recon' ? '정찰 드론' : '자폭 드론';
        sendCommand({ action: 'spawn', x, y, z: 0.3, lon: lng, lat, profile, role: droneRole }, `${label} 배치 → ${at}`);
      }
    },
    [profiles, profileId, droneRole, sendCommand, pickDraft, createTable, activeTableId, saveActive, tables]
  );

  const onDroneRemove = useCallback(
    (agentId) => sendCommand({ action: 'remove', agent_id: agentId }, `회수: ${agentId}`),
    [sendCommand]
  );

  const toggleArm = useCallback(
    () => sendCommand({ action: armed ? 'disarm' : 'arm' }, armed ? 'STANDBY' : '⚡ ACTIVATED — 드론 이륙'),
    [armed, sendCommand]
  );

  return (
    <div style={S.root}>
      {/* heavy WebGL base layer — memoized, never reconciled on text updates */}
      <TacticalMap
        telemetry={telemetry}
        basemap={basemap}
        buildTool={activeTool}
        invert={invert}
        onMapClick={onMapClick}
        onDroneRemove={onDroneRemove}
        battlefield={activeTable}
      />

      <DroneGarage open={garageOpen} onClose={() => setGarageOpen(false)} />

      <BattlefieldTable
        open={bfOpen}
        tables={tables}
        activeId={activeTableId}
        picking={picking}
        onStartPick={startPickCenter}
        onOpen={openTable}
        onDelete={deleteTable}
        onClose={() => setBfOpen(false)}
      />

      {builderOpen && (
        <div style={S.builderBar}>
          <span style={S.builderTitle}>⊕ 시나리오 편성</span>
          <div style={S.toolRow}>
            {[
              { id: 'enemy', label: '적 진지', c: '#ff4d4f' },
              { id: 'friendly', label: '아군 진영', c: '#40a9ff' },
              { id: 'drone', label: '드론 배치', c: '#7cffb2' },
              { id: 'remove', label: '회수', c: '#faad14' },
            ].map((t) => (
              <button
                key={t.id}
                onClick={() => setBuildTool(t.id)}
                style={{
                  ...S.toolBtn,
                  ...(buildTool === t.id
                    ? { background: '#13202d', color: t.c, boxShadow: `inset 0 0 0 1px ${t.c}` }
                    : {}),
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {buildTool === 'drone' && (
            <>
              <div style={S.roleToggle}>
                {[
                  { id: 'recon', label: '🛰 정찰', c: '#2f9bff' },
                  { id: 'kami', label: '💥 자폭', c: '#ff3b3b' },
                ].map((r) => (
                  <button
                    key={r.id}
                    onClick={() => setDroneRole(r.id)}
                    style={{
                      ...S.roleBtn,
                      ...(droneRole === r.id ? { background: '#13202d', color: r.c, boxShadow: `inset 0 0 0 1px ${r.c}` } : {}),
                    }}
                    title={r.id === 'recon' ? '정찰: 표적 상공 정찰 → 좌표 방송(킬체인 개시)' : '자폭: 방송 후 표적 돌입 타격'}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
              <select value={profileId} onChange={(e) => setProfileId(e.target.value)} style={S.builderSelect}>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.name} ({p.topology})</option>
                ))}
              </select>
            </>
          )}

          <button
            onClick={toggleArm}
            style={{ ...S.activateBtn, ...(armed ? S.activateBtnArmed : {}) }}
          >
            {armed ? '■ STANDBY' : '▶ ACTIVATE'}
          </button>
          <button style={S.clearBtn} onClick={() => sendCommand({ action: 'clear' }, '편성 초기화')}>초기화</button>

          <span style={{ ...S.statusTag, color: armed ? '#52c41a' : '#faad14' }}>
            {armed ? '● ARMED' : '○ STANDBY'}
          </span>
          {telemetry?.pool && (
            <span style={S.builderPool}>POOL {telemetry.pool.available}/{telemetry.pool.total}</span>
          )}
          {cmdMsg && <span style={S.builderMsg}>{cmdMsg}</span>}
        </div>
      )}

      {/* top status strip */}
      <div style={S.topbar}>
        <div style={S.brand}>
          <span style={S.brandMark}>◈ DEXIA</span>
          <span style={S.brandSub}>GROUND CONTROL STATION</span>
        </div>
        <div style={S.topRight}>
          <button
            style={{ ...S.garageBtn, ...(builderOpen ? S.deployBtnActive : {}) }}
            onClick={() => setBuilderOpen((v) => !v)}
            title="시나리오 편성 (적 진지/아군 진영/드론 배치)"
          >
            ⊕ 편성
          </button>
          <button
            style={{ ...S.garageBtn, ...(invert ? S.deployBtnActive : {}) }}
            onClick={() => setInvert((v) => !v)}
            title="Toggle tactical map colour inversion"
          >
            ◐ INVERT
          </button>
          <button
            style={{ ...S.garageBtn, ...(bfOpen ? S.deployBtnActive : {}) }}
            onClick={() => setBfOpen((v) => !v)}
            title="전장 테이블 — 구역을 저장하고 적/아군 배치를 기억"
          >
            ▦ 전장
          </button>
          <button style={S.garageBtn} onClick={() => setGarageOpen(true)}>◈ GARAGE</button>
          <BasemapSwitch value={basemap} onChange={setBasemap} />
          <Pill label={connected ? 'LINK ONLINE' : 'LINK DOWN'} color={connected ? '#52c41a' : '#ff4d4f'} />
          {telemetry && <Pill label={`TICK ${telemetry.tick}`} color="#40a9ff" />}
          {telemetry && (
            <Pill
              label={telemetry.events?.broadcast ? 'TGT BROADCAST' : 'RECON INBOUND'}
              color={telemetry.events?.broadcast ? '#52c41a' : '#8c8c8c'}
            />
          )}
        </div>
      </div>

      {/* LEFT — Swarm intelligence & targeting */}
      <div style={{ ...S.panelWrap, left: 0 }}>
        <LeftPanel telemetry={telemetry} />
      </div>

      {/* RIGHT — Tactical advisory & system telemetry */}
      <div style={{ ...S.panelWrap, right: 0, alignItems: 'flex-end' }}>
        <RightPanel telemetry={telemetry} />
      </div>

      {error && <div style={S.errorBar}>⚠ {error}</div>}

      {/* AIP Phase 8.7: HITL commander approval — gates AI doctrine changes */}
      <HitlApproval />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* LEFT PANEL — SWARM TELEMETRY + TARGET ACQUISITION                   */
/* ------------------------------------------------------------------ */
const LeftPanel = memo(function LeftPanel({ telemetry }) {
  const agents = telemetry?.agents || [];
  const broadcast = telemetry?.events?.broadcast;
  const target = telemetry?.target;

  return (
    <div style={{ ...S.card, width: 320 }}>
      <div style={S.cardTitle}>SWARM TELEMETRY</div>
      <div style={S.feed}>
        {agents.length === 0 && <div style={S.dim}>Awaiting telemetry…</div>}
        {agents.map((a) => (
          <div key={a.id} style={{ ...S.agentRow, opacity: a.lost ? 0.45 : 1 }}>
            <div style={S.agentHead}>
              <span style={{ ...S.dotTag, background: a.lost ? '#777' : a.kind === 'recon' ? '#2f9bff' : '#ff3b3b' }} />
              <span style={S.agentId}>{a.id}</span>
              <span style={{ ...S.agentState, color: a.lost ? '#ff4d4f' : '#52c41a' }}>
                {a.lost ? (a.loss_reason || 'LOST').toUpperCase() : 'ACTIVE'}
              </span>
            </div>
            <div style={S.agentStats}>
              <Stat k="ROLE" v={a.role} />
              <Stat k="ALT" v={`${a.alt.toFixed(1)} m`} />
              <Stat k="SPD" v={`${a.speed.toFixed(1)} m/s`} />
              <Stat k="SNR" v={`${a.snr_db.toFixed(0)} dB`} c={a.link_good ? '#cfe3ff' : '#faad14'} />
            </div>
          </div>
        ))}
      </div>

      <div style={{ ...S.cardTitle, marginTop: 14 }}>TARGET ACQUISITION</div>
      {broadcast && target ? (
        <div style={S.targetBox}>
          <div style={S.targetLock}>● COORDINATES LOCKED</div>
          <div style={S.targetCoords}>
            X {target[0].toFixed(2)} &nbsp; Y {target[1].toFixed(2)} &nbsp; Z {target[2].toFixed(2)}
          </div>
          <div style={S.dim}>Broadcast to strike group — clear to engage.</div>
        </div>
      ) : (
        <div style={S.dim}>Awaiting recon detection… target coordinates masked.</div>
      )}
    </div>
  );
});

/* ------------------------------------------------------------------ */
/* RIGHT PANEL — TACTICAL ADVISORY (AI STAFF) + SYSTEM TELEMETRY       */
/* ------------------------------------------------------------------ */
const RightPanel = memo(function RightPanel({ telemetry }) {
  const ns = telemetry?.network_survivability ?? 1;
  const wind = telemetry?.wind_gust ?? 0;
  const lost = telemetry?.events?.total_lost ?? 0;

  return (
    <div style={{ ...S.card, width: 340 }}>
      {/* AIP: real local-LLM OAG + Logic-block pipeline with commander approval */}
      <AIStaffCard />

      <div style={{ ...S.cardTitle, marginTop: 14 }}>SYSTEM TELEMETRY</div>
      <div style={S.gauges}>
        <Gauge label="NETWORK SURVIVABILITY" value={`${Math.round(ns * 100)}%`} color={ns > 0.7 ? '#52c41a' : ns > 0.4 ? '#faad14' : '#ff4d4f'} pct={ns * 100} />
        <Gauge label="WIND GUST" value={`${wind.toFixed(2)} N`} color="#40a9ff" pct={Math.min(100, (wind / 8) * 100)} />
        <Gauge label="ATTRITION" value={`${lost} / 6`} color={lost === 0 ? '#52c41a' : lost < 3 ? '#faad14' : '#ff4d4f'} pct={(lost / 6) * 100} />
      </div>

      {/* AIP Phase 9: EpisodeEvalSuite scorecard + immutable audit trails */}
      <EvalsPanel />
    </div>
  );
});

/* ------------------------------------------------------------------ */
/* small presentational helpers                                        */
/* ------------------------------------------------------------------ */
const BASEMAPS = [
  { id: 'satellite', label: '위성' },
  { id: 'hybrid', label: '하이브리드' },
  { id: 'topo', label: '지형' },
  { id: 'dark', label: '전술' },
];

function BasemapSwitch({ value, onChange }) {
  return (
    <div style={S.switchGroup}>
      {BASEMAPS.map((b) => {
        const active = value === b.id;
        return (
          <button
            key={b.id}
            type="button"
            onClick={() => onChange(b.id)}
            style={{
              ...S.switchBtn,
              background: active ? '#1e2b3a' : 'transparent',
              color: active ? '#7cc4ff' : '#8c97a3',
              boxShadow: active ? 'inset 0 0 0 1px #2f6da3' : 'none',
            }}
          >
            {b.label}
          </button>
        );
      })}
    </div>
  );
}

function Pill({ label, color }) {
  return <span style={{ ...S.pill, border: `1px solid ${color}`, color }}>{label}</span>;
}
function Stat({ k, v, c }) {
  return (
    <div style={S.stat}>
      <span style={S.statK}>{k}</span>
      <span style={{ ...S.statV, color: c || '#cfe3ff' }}>{v}</span>
    </div>
  );
}
function Gauge({ label, value, color, pct }) {
  return (
    <div style={S.gauge}>
      <div style={S.gaugeHead}>
        <span style={S.gaugeLabel}>{label}</span>
        <span style={{ ...S.gaugeValue, color }}>{value}</span>
      </div>
      <div style={S.gaugeTrack}>
        <div style={{ ...S.gaugeFill, width: `${Math.max(0, Math.min(100, pct))}%`, background: color }} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
const S = {
  root: { position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden', background: '#0b0f14', fontFamily: 'Segoe UI, system-ui, sans-serif', color: '#e6e6e6' },
  topbar: { position: 'absolute', top: 0, left: 0, right: 0, height: 48, zIndex: 40, display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 16px', background: 'linear-gradient(180deg, rgba(8,11,15,0.92), rgba(8,11,15,0))', pointerEvents: 'none' },
  brand: { display: 'flex', alignItems: 'baseline', gap: 10, pointerEvents: 'auto' },
  brandMark: { fontWeight: 800, letterSpacing: 2, color: '#40a9ff', fontSize: 20, textShadow: '0 0 12px rgba(64,169,255,0.5)' },
  brandSub: { color: '#6b7785', fontSize: 11, letterSpacing: 3 },
  topRight: { display: 'flex', gap: 8, alignItems: 'center', pointerEvents: 'auto' },
  pill: { padding: '4px 10px', borderRadius: 3, fontSize: 11, fontWeight: 700, letterSpacing: 1, background: 'rgba(8,11,15,0.6)' },
  garageBtn: { padding: '5px 12px', borderRadius: 5, fontSize: 11, fontWeight: 700, letterSpacing: 1, background: 'rgba(8,11,15,0.7)', border: '1px solid #2f6da3', color: '#7cc4ff', cursor: 'pointer' },
  deployBtnActive: { background: '#2f6da3', color: '#fff', boxShadow: '0 0 12px rgba(47,109,163,0.7)' },
  builderBar: { position: 'absolute', top: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 45, display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', background: 'rgba(13,18,24,0.94)', border: '1px solid #2f6da3', borderRadius: 8, pointerEvents: 'auto', boxShadow: '0 6px 24px rgba(0,0,0,0.55)', flexWrap: 'wrap', maxWidth: '94vw' },
  builderTitle: { color: '#7cc4ff', fontWeight: 800, fontSize: 12, letterSpacing: 1 },
  toolRow: { display: 'flex', gap: 4 },
  toolBtn: { background: 'transparent', color: '#8c97a3', border: '1px solid #243042', borderRadius: 5, padding: '6px 11px', cursor: 'pointer', fontSize: 12, fontWeight: 700 },
  builderSelect: { background: '#0a0e13', color: '#e6e6e6', border: '1px solid #243042', borderRadius: 5, padding: '5px 8px', fontSize: 12 },
  roleToggle: { display: 'flex', gap: 3 },
  roleBtn: { background: 'transparent', color: '#8c97a3', border: '1px solid #243042', borderRadius: 5, padding: '6px 9px', cursor: 'pointer', fontSize: 12, fontWeight: 700 },
  activateBtn: { background: '#1f7a3d', color: '#fff', border: 'none', borderRadius: 6, padding: '7px 16px', cursor: 'pointer', fontWeight: 800, fontSize: 13, letterSpacing: 0.5, boxShadow: '0 0 14px rgba(31,122,61,0.5)' },
  activateBtnArmed: { background: '#7a1f1f', boxShadow: '0 0 14px rgba(122,31,31,0.5)' },
  clearBtn: { background: 'transparent', color: '#8c97a3', border: '1px solid #243042', borderRadius: 5, padding: '6px 11px', cursor: 'pointer', fontSize: 12, fontWeight: 600 },
  statusTag: { fontSize: 11, fontWeight: 800, letterSpacing: 1 },
  builderPool: { color: '#52c41a', fontSize: 11, fontWeight: 700 },
  builderMsg: { color: '#faad14', fontSize: 11 },
  switchGroup: { display: 'flex', gap: 2, padding: 2, borderRadius: 5, background: 'rgba(8,11,15,0.7)', border: '1px solid #1f2933' },
  switchBtn: { padding: '4px 10px', borderRadius: 4, fontSize: 11, fontWeight: 700, letterSpacing: 0.5, border: 'none', cursor: 'pointer', transition: 'all 0.15s ease' },

  // panel containers: span the side, ignore pointer events so the map is draggable
  panelWrap: { position: 'absolute', top: 56, bottom: 12, zIndex: 40, display: 'flex', flexDirection: 'column', padding: 12, pointerEvents: 'none' },
  card: { pointerEvents: 'auto', background: 'rgba(13,18,24,0.82)', backdropFilter: 'blur(8px)', border: '1px solid #1f2933', borderRadius: 10, padding: 14, boxShadow: '0 8px 30px rgba(0,0,0,0.5)', maxHeight: '100%', overflowY: 'auto' },
  cardTitle: { fontSize: 11, letterSpacing: 2, color: '#6b7785', textTransform: 'uppercase', marginBottom: 10, paddingBottom: 6, borderBottom: '1px solid #1f2933' },

  feed: { display: 'flex', flexDirection: 'column', gap: 8 },
  dim: { color: '#5b6675', fontSize: 12, padding: '4px 0' },

  agentRow: { background: 'rgba(8,12,17,0.7)', border: '1px solid #161d25', borderRadius: 6, padding: '7px 9px' },
  agentHead: { display: 'flex', alignItems: 'center', gap: 7, marginBottom: 5 },
  dotTag: { width: 8, height: 8, borderRadius: '50%' },
  agentId: { fontSize: 12, fontWeight: 700, color: '#dbe6f2', flex: 1 },
  agentState: { fontSize: 10, fontWeight: 700, letterSpacing: 0.5 },
  agentStats: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3px 10px' },
  stat: { display: 'flex', justifyContent: 'space-between', fontSize: 11 },
  statK: { color: '#5b6675' },
  statV: { fontWeight: 600 },

  targetBox: { background: 'rgba(245,34,45,0.08)', border: '1px solid rgba(245,34,45,0.35)', borderRadius: 6, padding: 10 },
  targetLock: { color: '#ff6b6b', fontSize: 11, fontWeight: 700, letterSpacing: 1, marginBottom: 4 },
  targetCoords: { fontFamily: 'Consolas, monospace', fontSize: 14, color: '#ffd7d7', marginBottom: 4 },

  advisory: { background: 'rgba(8,12,17,0.7)', padding: '8px 11px', borderRadius: 4, fontSize: 12.5, lineHeight: 1.45 },

  gauges: { display: 'flex', flexDirection: 'column', gap: 10 },
  gauge: {},
  gaugeHead: { display: 'flex', justifyContent: 'space-between', marginBottom: 4 },
  gaugeLabel: { fontSize: 10.5, letterSpacing: 1, color: '#6b7785' },
  gaugeValue: { fontSize: 13, fontWeight: 700 },
  gaugeTrack: { height: 6, borderRadius: 3, background: '#161d25', overflow: 'hidden' },
  gaugeFill: { height: '100%', borderRadius: 3, transition: 'width 0.3s ease' },

  errorBar: { position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 50, background: 'rgba(42,18,21,0.95)', color: '#ff7875', padding: '8px 16px', fontSize: 12, pointerEvents: 'none' },
};
