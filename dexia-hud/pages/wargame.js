// pages/wargame.js — AIP Wargame HUD (Build #8)
//
// Layout:
//   ┌────────────────────────────────────────────────────────────┐
//   │  ◈ DEXIA · WARGAME REPLAY              [basemap ▾] [GCS]  │  topbar
//   ├──────────────────┬──────────────────────────────┬──────────┤
//   │  LEFT RAIL       │   CENTER MAP                 │  RIGHT   │
//   │  ScenarioPicker  │   WargameMap                 │  RAIL    │
//   │  ─────────────── │   TrackLayer                 │          │
//   │  CampaignScore   │   LosOverlay (3D only)       │ Reasoning│
//   │  board           │   TrajectoryLayer (3D only)  │ Timeline │
//   └──────────────────┴──────────────────────────────┴──────────┘
//
// Shared state:
//   cycles[]    — reasoning_trace.jsonl records (one per AI decision cycle)
//   snapshots[] — world_snapshot.jsonl records (1:1 with cycles)
//   cycleIndex  — currently displayed cycle (drives map + timeline highlight)
//   playing     — auto-advance timer
//
// MapLibre is loaded client-side only (dynamic import, ssr:false).

import { useState, useEffect, useRef, useCallback } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { theaterAnchor } from '../lib/geo';
import ReasoningTimeline  from '../components/wargame/ReasoningTimeline';
import ScenarioPicker     from '../components/wargame/ScenarioPicker';
import CampaignScoreboard from '../components/wargame/CampaignScoreboard';
import TrackLayer         from '../components/wargame/TrackLayer';
import LosOverlay         from '../components/wargame/LosOverlay';
import TrajectoryLayer    from '../components/wargame/TrajectoryLayer';

// MapLibre touches `window` — client-side only
const WargameMap = dynamic(() => import('../components/wargame/WargameMap'), { ssr: false });

const PLAY_INTERVAL_MS = 1800;

const BASEMAPS = [
  { id: 'dark',      label: '전술' },
  { id: 'satellite', label: '위성' },
  { id: 'hybrid',    label: '하이브리드' },
  { id: 'topo',      label: '지형' },
];

export default function WargamePage() {
  const [cycles,       setCycles]      = useState([]);
  const [snapshots,    setSnapshots]   = useState([]);
  const [cycleIndex,   setCycleIndex]  = useState(0);
  const [playing,      setPlaying]     = useState(false);
  const [basemap,      setBasemap]     = useState('dark');
  const [anchor,       setAnchor]      = useState(theaterAnchor('eastern_europe'));
  const [selectedId,   setSelectedId]  = useState(null);
  const [showScoreboard, setShowScoreboard] = useState(false);

  const mapRef      = useRef(null);   // populated by WargameMap.onReady
  const playRef     = useRef(null);

  // ---- Play timer --------------------------------------------------------
  useEffect(() => {
    if (playing && cycles.length > 0) {
      playRef.current = setInterval(() => {
        setCycleIndex(i => {
          if (i >= cycles.length - 1) {
            setPlaying(false);
            return i;
          }
          return i + 1;
        });
      }, PLAY_INTERVAL_MS);
    }
    return () => clearInterval(playRef.current);
  }, [playing, cycles.length]);

  const togglePlay = useCallback(() => setPlaying(p => !p), []);

  const selectCycle = useCallback((i) => {
    setPlaying(false);
    setCycleIndex(Math.max(0, Math.min(i, cycles.length - 1)));
  }, [cycles.length]);

  // ---- On scenario run complete ------------------------------------------
  const handleRunComplete = useCallback(({ cycles: c, snapshots: s, theater, hasTerrain, scenarioId }) => {
    setCycles(c);
    setSnapshots(s);
    setCycleIndex(0);
    setPlaying(false);
    setAnchor(theaterAnchor(theater));
    setSelectedId(scenarioId);
  }, []);

  const currentCycle    = cycles[cycleIndex]    || null;
  const currentSnapshot = snapshots[cycleIndex] || null;
  const hasTerrain      = currentSnapshot?.has_terrain || false;

  return (
    <div style={S.root}>
      {/* ---- Top bar ---------------------------------------------------- */}
      <div style={S.topbar}>
        <div style={S.brand}>
          <span style={S.brandMark}>◈ DEXIA</span>
          <span style={S.brandSep}>·</span>
          <span style={S.brandSub}>WARGAME REPLAY</span>
          {selectedId && (
            <span style={S.scenarioPill}>{selectedId}</span>
          )}
          {hasTerrain && (
            <span style={S.terrainBadge}>3D TERRAIN</span>
          )}
        </div>
        <div style={S.topRight}>
          {/* Cycle progress if loaded */}
          {cycles.length > 0 && (
            <span style={S.cyclePill}>
              CYCLE {cycleIndex + 1}/{cycles.length}
            </span>
          )}
          {/* Basemap switch */}
          <div style={S.switchGroup}>
            {BASEMAPS.map(b => (
              <button
                key={b.id}
                onClick={() => setBasemap(b.id)}
                style={{
                  ...S.switchBtn,
                  background: basemap === b.id ? '#1e2b3a' : 'transparent',
                  color: basemap === b.id ? '#7cc4ff' : '#6b7785',
                  boxShadow: basemap === b.id ? 'inset 0 0 0 1px #2f6da3' : 'none',
                }}
              >{b.label}</button>
            ))}
          </div>
          {/* Scoreboard toggle */}
          <button
            style={{ ...S.topBtn, ...(showScoreboard ? S.topBtnActive : {}) }}
            onClick={() => setShowScoreboard(v => !v)}
          >📊 스코어</button>
          {/* Back to GCS */}
          <Link href="/" style={S.gcsLink}>◈ GCS</Link>
        </div>
      </div>

      {/* ---- Left rail -------------------------------------------------- */}
      <div style={{ ...S.rail, left: 0 }}>
        {/* ScenarioPicker always shown */}
        <div style={{
          flex: showScoreboard ? '0 0 50%' : '1 1 auto',
          minHeight: 0, overflow: 'hidden', pointerEvents: 'auto',
        }}>
          <ScenarioPicker
            onRunComplete={handleRunComplete}
            selectedId={selectedId}
          />
        </div>
        {/* CampaignScoreboard toggle */}
        {showScoreboard && (
          <div style={{ flex: '0 0 48%', minHeight: 0, overflow: 'hidden', pointerEvents: 'auto' }}>
            <CampaignScoreboard />
          </div>
        )}
      </div>

      {/* ---- Centre map (full screen, under everything) ----------------- */}
      <div style={S.mapContainer}>
        <WargameMap
          anchor={anchor}
          basemap={basemap}
          onReady={(map) => { mapRef.current = map; }}
        />
      </div>

      {/* ---- Right rail — ReasoningTimeline ----------------------------- */}
      <div style={{ ...S.rail, right: 0, width: 340 }}>
        <div style={{ flex: 1, minHeight: 0, pointerEvents: 'auto' }}>
          <ReasoningTimeline
            cycles={cycles}
            cycleIndex={cycleIndex}
            onCycleSelect={selectCycle}
            playing={playing}
            onTogglePlay={togglePlay}
          />
        </div>
      </div>

      {/* ---- Map overlay layers (React re-render triggers) -------------- */}
      {/* These useEffect-only components need a stable parent ref */}
      <MapLayerDriver
        mapRef={mapRef}
        currentCycle={currentCycle}
        currentSnapshot={currentSnapshot}
        anchor={anchor}
      />
    </div>
  );
}

// MapLayerDriver keeps the imperative overlay components alive and re-running
// their useEffect hooks when cycleIndex changes, without being inside the
// absolute-positioned map container (which would confuse DOM z-index).
function MapLayerDriver({ mapRef, currentCycle, currentSnapshot, anchor }) {
  const [map, setMap] = useState(null);

  useEffect(() => {
    // Poll until mapRef has been populated by WargameMap.onReady
    let cancelled = false;
    const poll = setInterval(() => {
      if (mapRef.current && !cancelled) {
        setMap(mapRef.current);
        clearInterval(poll);
      }
    }, 200);
    return () => { cancelled = true; clearInterval(poll); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!map) return null;
  return (
    <>
      <TrackLayer      map={map} cycle={currentCycle}    anchor={anchor} />
      <LosOverlay      map={map} snapshot={currentSnapshot} anchor={anchor} />
      <TrajectoryLayer map={map} cycle={currentCycle}    anchor={anchor} />
    </>
  );
}

/* ------------------------------------------------------------------ */
const S = {
  root: {
    position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden',
    background: '#090d12',
    fontFamily: "'Segoe UI', system-ui, sans-serif", color: '#e6e6e6',
  },
  topbar: {
    position: 'absolute', top: 0, left: 0, right: 0, height: 48, zIndex: 40,
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '0 16px',
    background: 'linear-gradient(180deg, rgba(6,9,14,0.95), rgba(6,9,14,0))',
    pointerEvents: 'none',
  },
  brand: {
    display: 'flex', alignItems: 'center', gap: 8,
    pointerEvents: 'auto',
  },
  brandMark: {
    fontWeight: 800, letterSpacing: 2, color: '#40a9ff', fontSize: 18,
    textShadow: '0 0 12px rgba(64,169,255,0.5)',
  },
  brandSep: { color: '#2a3441', fontSize: 16 },
  brandSub: { color: '#5b6675', fontSize: 11, letterSpacing: 3, textTransform: 'uppercase' },
  scenarioPill: {
    padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 700,
    background: 'rgba(30,58,100,0.5)', border: '1px solid #3b82f6',
    color: '#93c5fd', letterSpacing: 0.3,
  },
  terrainBadge: {
    padding: '2px 8px', borderRadius: 4, fontSize: 9, fontWeight: 800,
    background: 'rgba(20,83,45,0.5)', border: '1px solid #22c55e',
    color: '#86efac', letterSpacing: 1,
  },
  topRight: {
    display: 'flex', gap: 8, alignItems: 'center',
    pointerEvents: 'auto',
  },
  cyclePill: {
    padding: '3px 10px', borderRadius: 4, fontSize: 10, fontWeight: 700,
    background: 'rgba(8,11,15,0.7)', border: '1px solid #2f6da3',
    color: '#7cc4ff', letterSpacing: 1,
  },
  switchGroup: {
    display: 'flex', gap: 2, padding: 2, borderRadius: 5,
    background: 'rgba(8,11,15,0.7)', border: '1px solid #1f2933',
  },
  switchBtn: {
    padding: '4px 9px', borderRadius: 4, fontSize: 10, fontWeight: 700,
    letterSpacing: 0.5, border: 'none', cursor: 'pointer', transition: 'all 0.15s ease',
  },
  topBtn: {
    padding: '5px 11px', borderRadius: 5, fontSize: 10, fontWeight: 700,
    background: 'rgba(8,11,15,0.7)', border: '1px solid #2f6da3',
    color: '#7cc4ff', cursor: 'pointer', letterSpacing: 0.5,
  },
  topBtnActive: { background: '#2f6da3', color: '#fff' },
  gcsLink: {
    padding: '5px 11px', borderRadius: 5, fontSize: 10, fontWeight: 700,
    background: 'rgba(8,11,15,0.7)', border: '1px solid #1f2933',
    color: '#6b7785', textDecoration: 'none', letterSpacing: 1,
  },

  // Side rails — absolute columns, pointer events off so map is draggable
  rail: {
    position: 'absolute', top: 52, bottom: 12, zIndex: 40,
    width: 320, display: 'flex', flexDirection: 'column',
    padding: 10, gap: 8,
    pointerEvents: 'none',
  },
  // Override rail pointerEvents for interactive children
  // (applied via inline style on individual child panels)

  mapContainer: {
    position: 'absolute', inset: 0,
    zIndex: 10,
  },
};

// Make rail children interactive despite rail having pointerEvents:none
// by overriding in each child component's root style.
// (The rail itself is pointer-none so the underlying map stays draggable
//  through the rail's transparent background area.)
