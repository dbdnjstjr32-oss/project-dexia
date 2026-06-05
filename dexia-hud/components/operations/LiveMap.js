import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

const M2DEG = 1 / 111000;

// Fallback capability envelopes (m) so rings render even before the backend
// ships per-asset sensor/weapon ranges. Keyed by a substring of the class id.
const SENSOR_FALLBACK = { tb2_recon_uav: 8000, switchblade: 3000, ugs_field: 1500, ground_radar: 25000 };
const WEAPON_FALLBACK = { switchblade: { range_m: 10000, min_range_m: 0, lethal_r: 15 },
                          m777_howitzer: { range_m: 30000, min_range_m: 4000, lethal_r: 70 } };

// Classify an asset for marker colour + ring meaning.
function kindOf(b) {
  const cls = (b.cls || '').toLowerCase();
  if (cls.includes('switchblade') || (b.weapon && b.weapon.type === 'loiter_strike')) return 'loiter';
  if (cls.includes('uav') || b.role === 'isr' || b.domain === 'air') return 'recon';
  if (b.role === 'fires' || (b.weapon && b.weapon.type === 'indirect_fire')) return 'fires';
  return 'other';
}
function sensorRange(b) {
  if (b.sensor && b.sensor.range_m) return b.sensor.range_m;
  const k = Object.keys(SENSOR_FALLBACK).find((key) => (b.cls || '').includes(key));
  return k ? SENSOR_FALLBACK[k] : 0;
}
function weaponSpec(b) {
  if (b.weapon && b.weapon.range_m) return b.weapon;
  const k = Object.keys(WEAPON_FALLBACK).find((key) => (b.cls || '').includes(key));
  return k ? WEAPON_FALLBACK[k] : null;
}

// Enemy category -> human-readable Korean name shown on the map label.
const CATEGORY_KO = {
  armor: '기갑', apc: '장갑차', infantry: '보병', artillery: '포병',
  air_defense: '방공', emitter: '발신원', ew: '전자전', air: '항공기', structure: '구조물',
};
function trackLabel(t) {
  const name = CATEGORY_KO[t.category] || t.category || '미상';
  const shortId = (t.track_id || '').replace(/^TRK-/, '');
  const conf = Math.round((t.confidence || 0) * 100);
  // low confidence => "분석중" so the operator knows it isn't confirmed yet
  const tag = conf < 50 ? '분석중' : `${conf}%`;
  return `${name} ·${shortId} (${tag})`;
}

export default function LiveMap({ gameState }) {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const flewTo = useRef(false);
  const historyRef = useRef({ blue: {}, red: {} });
  const labelMarkers = useRef({});   // track_id -> maplibregl.Marker (HTML name labels)

  useEffect(() => {
    if (map.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
        sources: {
          'osm-dark': {
            type: 'raster',
            tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
            tileSize: 256,
          },
        },
        layers: [{ id: 'basemap', type: 'raster', source: 'osm-dark', paint: { 'raster-opacity': 0.8 } }],
      },
      center: [127.0, 38.0],
      zoom: 12.5,
      pitch: 45,
      interactive: true,
      // Render Hangul/CJK labels with local system fonts (no CJK glyph PBFs needed).
      localIdeographFontFamily: "'Malgun Gothic', 'Noto Sans KR', sans-serif",
    });

    map.current.on('load', () => {
      const src = (id) => map.current.addSource(id, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

      // Fog of war: the unconfirmed enemy area (drawn first, under everything)
      src('fog');
      map.current.addLayer({ id: 'fog-fill', type: 'fill', source: 'fog',
        paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.16 } });
      map.current.addLayer({ id: 'fog-line', type: 'line', source: 'fog',
        paint: { 'line-color': '#f59e0b', 'line-width': 1.5, 'line-dasharray': [3, 2], 'line-opacity': 0.6 } });

      // Detection range — every sensor asset's coverage (cyan). Drawn under units.
      src('detection');
      map.current.addLayer({ id: 'detection-fill', type: 'fill', source: 'detection',
        paint: { 'fill-color': '#22d3ee', 'fill-opacity': 0.07 } });
      map.current.addLayer({ id: 'detection-line', type: 'line', source: 'detection',
        paint: { 'line-color': '#22d3ee', 'line-width': 1.2, 'line-opacity': 0.45 } });

      // Weapon range — engagement envelope (amber = loiter munition, orange = artillery).
      src('weapon');
      map.current.addLayer({ id: 'weapon-line', type: 'line', source: 'weapon',
        paint: { 'line-color': ['get', 'color'], 'line-width': 1.4,
                 'line-dasharray': [2, 2], 'line-opacity': 0.55 } });

      // Trails (under the dots)
      src('blue-trails');
      map.current.addLayer({ id: 'blue-trails-layer', type: 'line', source: 'blue-trails',
        paint: { 'line-color': '#3b82f6', 'line-width': 2, 'line-opacity': 0.6, 'line-dasharray': [2, 1] } });
      src('red-trails');
      map.current.addLayer({ id: 'red-trails-layer', type: 'line', source: 'red-trails',
        paint: { 'line-color': '#ef4444', 'line-width': 2, 'line-opacity': 0.6, 'line-dasharray': [2, 1] } });

      // Munitions in flight (artillery / switchblade strike path)
      src('munitions');
      map.current.addLayer({ id: 'munitions-layer', type: 'line', source: 'munitions',
        paint: { 'line-color': ['get', 'color'], 'line-width': 3, 'line-opacity': 0.95, 'line-dasharray': [1, 1.5] } });

      // Predicted impact point + blast ring (pulses while the round is inbound)
      src('impacts');
      map.current.addLayer({ id: 'impact-ring', type: 'circle', source: 'impacts',
        paint: { 'circle-radius': ['get', 'pulse'], 'circle-color': ['get', 'color'],
                 'circle-opacity': 0.18, 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 1.5 } });
      map.current.addLayer({ id: 'impact-core', type: 'circle', source: 'impacts',
        paint: { 'circle-radius': 4, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });

      // Enemy tracks (uncertainty halo + dot)
      src('red-tracks');
      map.current.addLayer({ id: 'red-uncertainty', type: 'circle', source: 'red-tracks',
        paint: { 'circle-radius': ['max', 5, ['/', ['get', 'unc'], 25]], 'circle-color': '#ef4444', 'circle-opacity': 0.12 } });
      map.current.addLayer({ id: 'red-layer', type: 'circle', source: 'red-tracks',
        paint: {
          'circle-radius': 7,
          'circle-color': '#ef4444',
          'circle-stroke-width': 2.5,
          'circle-stroke-color': '#fecaca',
        } });
      // NOTE: enemy name labels are rendered as HTML markers (see labelMarkers),
      // not a symbol layer — that avoids the external glyph-server dependency and
      // renders Hangul reliably with the browser's own fonts.

      // Blue units, coloured by kind (on top). Switchblade gets a bright amber dot.
      src('blue-units');
      map.current.addLayer({ id: 'blue-layer', type: 'circle', source: 'blue-units',
        paint: {
          'circle-radius': ['match', ['get', 'kind'], 'loiter', 7, 'recon', 7, 6],
          'circle-color': ['match', ['get', 'kind'],
            'recon', '#22d3ee', 'loiter', '#f59e0b', 'fires', '#3b82f6', '#60a5fa'],
          'circle-stroke-width': 2, 'circle-stroke-color': '#fff',
        } });
    });

    return () => {
      Object.values(labelMarkers.current).forEach((m) => m.remove());
      labelMarkers.current = {};
      if (map.current) map.current.remove();
      map.current = null;
      flewTo.current = false;
      historyRef.current = { blue: {}, red: {} };
    };
  }, []);

  useEffect(() => {
    if (!map.current || !map.current.isStyleLoaded()) return;
    const setData = (id, features) => {
      const s = map.current.getSource(id);
      if (s) s.setData({ type: 'FeatureCollection', features });
    };

    if (!gameState) {
      historyRef.current = { blue: {}, red: {} };
      flewTo.current = false;
      Object.values(labelMarkers.current).forEach((m) => m.remove());
      labelMarkers.current = {};
      ['blue-units', 'red-tracks', 'blue-trails', 'red-trails', 'munitions', 'fog',
       'detection', 'weapon', 'impacts'].forEach((id) => setData(id, []));
      return;
    }

    const origin = (gameState.battlefield && gameState.battlefield.location) || [127.0, 38.0];
    const [originLon, originLat] = origin;
    const toLngLat = (x, y) => [originLon + M2DEG * (x || 0), originLat + M2DEG * (y || 0)];
    // Circle polygon in local x/y metres, projected through the same transform so
    // rings stay concentric with the unit that owns them.
    const ringFeature = (cx, cy, r, props, steps = 64) => {
      if (!r || r <= 0) return null;
      const coords = [];
      for (let i = 0; i <= steps; i++) {
        const a = (i / steps) * 2 * Math.PI;
        coords.push(toLngLat(cx + r * Math.cos(a), cy + r * Math.sin(a)));
      }
      return { type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [coords] } };
    };

    const details = gameState.blue_details || [];

    const blueFeatures = details.map((b) => ({
      type: 'Feature', properties: { id: b.id, cls: b.cls, kind: kindOf(b) },
      geometry: { type: 'Point', coordinates: toLngLat(b.pos[0], b.pos[1]) },
    }));

    // Detection rings (cyan) — one per sensor-bearing asset.
    const detectionFeatures = details
      .map((b) => ringFeature(b.pos[0], b.pos[1], sensorRange(b), { id: b.id }))
      .filter(Boolean);

    // Weapon rings — amber for loiter munition, orange for artillery; plus the
    // artillery min-range dead-zone ring.
    const weaponFeatures = [];
    details.forEach((b) => {
      const w = weaponSpec(b);
      if (!w) return;
      const isLoiter = kindOf(b) === 'loiter';
      const color = isLoiter ? '#f59e0b' : '#fb923c';
      const outer = ringFeature(b.pos[0], b.pos[1], w.range_m, { id: b.id, color });
      if (outer) weaponFeatures.push(outer);
      if (w.min_range_m > 0) {
        const inner = ringFeature(b.pos[0], b.pos[1], w.min_range_m, { id: b.id + '-min', color });
        if (inner) weaponFeatures.push(inner);
      }
    });

    const redFeatures = (gameState.tracks || []).map((t) => ({
      type: 'Feature',
      properties: { id: t.track_id, conf: t.confidence, unc: t.uncertainty_r || 50,
                    category: t.category, label: trackLabel(t) },
      geometry: { type: 'Point', coordinates: toLngLat(t.position[0], t.position[1]) },
    }));

    // Movement trails
    const hist = historyRef.current;
    details.forEach((b) => {
      if (!hist.blue[b.id]) hist.blue[b.id] = [];
      const coord = toLngLat(b.pos[0], b.pos[1]);
      const last = hist.blue[b.id][hist.blue[b.id].length - 1];
      if (!last || last[0] !== coord[0] || last[1] !== coord[1]) {
        hist.blue[b.id].push(coord);
        if (hist.blue[b.id].length > 100) hist.blue[b.id].shift();
      }
    });
    (gameState.tracks || []).forEach((t) => {
      if (!hist.red[t.track_id]) hist.red[t.track_id] = [];
      const coord = toLngLat(t.position[0], t.position[1]);
      const last = hist.red[t.track_id][hist.red[t.track_id].length - 1];
      if (!last || last[0] !== coord[0] || last[1] !== coord[1]) {
        hist.red[t.track_id].push(coord);
        if (hist.red[t.track_id].length > 100) hist.red[t.track_id].shift();
      }
    });
    const trailFC = (store) => Object.entries(store).filter(([, c]) => c.length > 1)
      .map(([id, coords]) => ({ type: 'Feature', properties: { id }, geometry: { type: 'LineString', coordinates: coords } }));

    // Munitions in flight + predicted impact markers (pulse animated by tick).
    const trajs = gameState.trajectories || [];
    const pulse = 14 + 6 * Math.sin((gameState.tick || 0) * 0.6);  // breathing blast ring
    const munitionFeatures = trajs.map((t) => ({
      type: 'Feature',
      properties: { color: t.action === 'fire' ? '#fb923c' : '#fde047' }, // orange arty / yellow strike
      geometry: { type: 'LineString', coordinates: (t.path || []).map((p) => toLngLat(p[0], p[1])) },
    }));
    const impactFeatures = trajs.map((t) => {
      const path = t.path || [];
      const end = path[path.length - 1];
      if (!end) return null;
      return { type: 'Feature',
        properties: { color: t.action === 'fire' ? '#fb923c' : '#fde047', pulse },
        geometry: { type: 'Point', coordinates: toLngLat(end[0], end[1]) } };
    }).filter(Boolean);

    setData('blue-units', blueFeatures);
    setData('detection', detectionFeatures);
    setData('weapon', weaponFeatures);
    setData('red-tracks', redFeatures);

    // Enemy name labels as HTML markers (reliable Hangul, no glyph server).
    const tracks = gameState.tracks || [];
    const liveIds = new Set(tracks.map((t) => t.track_id));
    Object.keys(labelMarkers.current).forEach((id) => {
      if (!liveIds.has(id)) { labelMarkers.current[id].remove(); delete labelMarkers.current[id]; }
    });
    tracks.forEach((t) => {
      const lngLat = toLngLat(t.position[0], t.position[1]);
      const text = trackLabel(t);
      let mk = labelMarkers.current[t.track_id];
      if (!mk) {
        const el = document.createElement('div');
        el.style.cssText = 'color:#fecaca;font:600 11px/1.2 "Malgun Gothic",sans-serif;' +
          'text-shadow:0 0 3px #000,0 0 3px #000,0 1px 2px #450a0a;white-space:nowrap;' +
          'pointer-events:none;transform:translateY(2px);';
        mk = new maplibregl.Marker({ element: el, anchor: 'top', offset: [0, 9] }).setLngLat(lngLat).addTo(map.current);
        labelMarkers.current[t.track_id] = mk;
      }
      if (mk.getElement().textContent !== text) mk.getElement().textContent = text;
      mk.setLngLat(lngLat);
    });
    setData('blue-trails', trailFC(hist.blue));
    setData('red-trails', trailFC(hist.red));
    setData('munitions', munitionFeatures);
    setData('impacts', impactFeatures);

    // Fog polygon over the enemy band
    const ea = gameState.enemy_area;
    const fogFeatures = ea ? [{
      type: 'Feature', properties: {},
      geometry: { type: 'Polygon', coordinates: [[
        toLngLat(ea.x0, ea.y0), toLngLat(ea.x1, ea.y0),
        toLngLat(ea.x1, ea.y1), toLngLat(ea.x0, ea.y1), toLngLat(ea.x0, ea.y0),
      ]] },
    }] : [];
    setData('fog', fogFeatures);

    if (!flewTo.current && (blueFeatures.length || ea)) {
      flewTo.current = true;
      map.current.flyTo({ center: toLngLat(0, ea ? (ea.y0 + ea.y1) / 2 / 2 : 0), zoom: 11.5 });
    }
  }, [gameState]);

  return (
    <div className="w-full h-full relative">
      <div ref={mapContainer} className="absolute inset-0" />
      <div className="absolute top-4 left-4 bg-gray-900/80 border border-amber-500/50 text-amber-400 px-3 py-1 rounded text-xs font-mono backdrop-blur">
        FOG OF WAR — 적군 지역 미확인 (정찰 필요)
      </div>

      {/* Legend — explains the rings & markers */}
      <div className="absolute bottom-4 left-4 bg-gray-900/85 border border-gray-700 rounded px-3 py-2 text-[11px] text-gray-300 font-mono backdrop-blur space-y-1">
        <div className="text-gray-500 font-bold tracking-wider mb-1">범례</div>
        <Row color="#22d3ee" label="정찰드론 (TB2) · 탐지 8km" />
        <Row color="#f59e0b" label="Switchblade · 타격 10km" dashed />
        <Row color="#fb923c" label="곡사포 (M777) · 4–30km" dashed />
        <Row color="#fde047" label="배회폭탄 비행 궤적" />
        <Row color="#ef4444" label="적 표적 (불확실성 ◯)" />
      </div>
    </div>
  );
}

function Row({ color, label, dashed }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block w-4 h-0" style={{
        borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}`,
      }} />
      <span style={{ color }}>{label}</span>
    </div>
  );
}
