// TacticalMap — full-screen MapLibre GL base layer with imperative tactical
// overlays + bi-directional C2 (deploy/remove via map clicks).
//
// `mapcn` is a shadcn wrapper over MapLibre GL; this builds directly on the
// underlying maplibre-gl engine it wraps. The map is created ONCE; all overlays
// (AoE circles, EW area, targeting line, drone markers, AA ammo popup) update
// IMPERATIVELY (setData / setPaintProperty / Marker.setLngLat) so high-frequency
// telemetry never triggers a React reconcile of the WebGL canvas.

import { memo, useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import {
  GEO_ANCHOR,
  INITIAL_ZOOM,
  localToLngLat,
  threatCircleGeoJSON,
  lineGeoJSON,
  gridGeoJSON,
} from '../lib/geo';

const ESRI_SAT =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const ESRI_REF =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';
const CARTO_DARK = [
  'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
  'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
  'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
];
const OPENTOPO = [
  'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
  'https://b.tile.opentopomap.org/{z}/{x}/{y}.png',
  'https://c.tile.opentopomap.org/{z}/{x}/{y}.png',
];
// AWS Terrain Tiles (Terrarium-encoded DEM) — public, no API key.
const TERRARIUM_DEM = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const TERRAIN_EXAGGERATION = 1.5;
// 3D terrain toggle — OFF: flat top-down 2D map (no DEM extrusion, no hillshade).
const ENABLE_TERRAIN = false;
const INITIAL_PITCH = ENABLE_TERRAIN ? 60 : 0;
const INITIAL_BEARING = ENABLE_TERRAIN ? -20 : 0;

const THREAT_COLOR = { idle: '#22c55e', radar: '#eab308', kill: '#ef4444' };
const EMPTY = { type: 'FeatureCollection', features: [] };

function visFor(basemap) {
  return {
    'bm-sat': basemap === 'satellite' || basemap === 'hybrid' ? 'visible' : 'none',
    'bm-ref': basemap === 'hybrid' ? 'visible' : 'none',
    'bm-topo': basemap === 'topo' ? 'visible' : 'none',
    'bm-dark': basemap === 'dark' ? 'visible' : 'none',
  };
}
// Hillshade adds shaded relief; only when 3D terrain is enabled (flat map -> off).
function hillVis(basemap) {
  if (!ENABLE_TERRAIN) return 'none';
  return basemap === 'dark' || basemap === 'topo' ? 'visible' : 'none';
}
function applyBasemap(map, basemap) {
  for (const [id, visibility] of Object.entries(visFor(basemap))) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility);
  }
  if (map.getLayer('hillshade')) {
    map.setLayoutProperty('hillshade', 'visibility', hillVis(basemap));
  }
}
function buildStyle(basemap) {
  const v = visFor(basemap);
  return {
    version: 8,
    sources: {
      'bm-sat': { type: 'raster', tiles: [ESRI_SAT], tileSize: 256, maxzoom: 19, attribution: 'Imagery © Esri, Maxar' },
      'bm-ref': { type: 'raster', tiles: [ESRI_REF], tileSize: 256, maxzoom: 19, attribution: '© Esri' },
      'bm-topo': { type: 'raster', tiles: OPENTOPO, tileSize: 256, maxzoom: 17, attribution: '© OpenTopoMap (CC-BY-SA)' },
      'bm-dark': { type: 'raster', tiles: CARTO_DARK, tileSize: 256, attribution: '© OpenStreetMap © CARTO' },
      // Digital Elevation Model -> drives 3D terrain + hillshade.
      'terrain-dem': {
        type: 'raster-dem', tiles: [TERRARIUM_DEM], encoding: 'terrarium',
        tileSize: 256, maxzoom: 15, attribution: '© AWS Terrain Tiles / Mapzen',
      },
    },
    layers: [
      { id: 'bm-sat', type: 'raster', source: 'bm-sat', layout: { visibility: v['bm-sat'] } },
      { id: 'bm-ref', type: 'raster', source: 'bm-ref', layout: { visibility: v['bm-ref'] } },
      { id: 'bm-topo', type: 'raster', source: 'bm-topo', layout: { visibility: v['bm-topo'] } },
      { id: 'bm-dark', type: 'raster', source: 'bm-dark', layout: { visibility: v['bm-dark'] } },
      {
        id: 'hillshade', type: 'hillshade', source: 'terrain-dem',
        layout: { visibility: hillVis(basemap) },
        paint: {
          'hillshade-exaggeration': 0.55,
          'hillshade-shadow-color': '#04070b',
          'hillshade-highlight-color': '#3a4a5a',
          'hillshade-accent-color': '#0a0e13',
        },
      },
    ],
  };
}

// MIL-STD-2525-style unit symbol: friendly = blue rectangle, hostile = red
// diamond, with a callsign + equipment label beneath.
function makeUnitEl() {
  const el = document.createElement('div');
  el.className = 'dx-unit';
  const shape = document.createElement('div');
  shape.className = 'dx-shape';
  const name = document.createElement('div');
  name.className = 'dx-name';
  const equip = document.createElement('div');
  equip.className = 'dx-equip';
  el.appendChild(shape);
  el.appendChild(name);
  el.appendChild(equip);
  return { el, name, equip };
}

function TacticalMapImpl({ telemetry, basemap = 'satellite', buildTool = 'off', invert = false, onMapClick, onDroneRemove }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const markersRef = useRef({});
  const loadedRef = useRef(false);
  const overlaysRef = useRef(false);
  const aaPopupRef = useRef(null);
  const friendlyMarkerRef = useRef(null);

  // latest-prop refs so the one-time event handlers never go stale
  const basemapRef = useRef(basemap); basemapRef.current = basemap;
  const toolRef = useRef(buildTool); toolRef.current = buildTool;     // 'off'|'enemy'|'friendly'|'drone'|'remove'
  const onClickRef = useRef(onMapClick); onClickRef.current = onMapClick;
  const onRemoveRef = useRef(onDroneRemove); onRemoveRef.current = onDroneRemove;

  // --- create the map once + add overlay sources/layers ----------------
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(basemapRef.current),
      center: [GEO_ANCHOR.lon, GEO_ANCHOR.lat],
      // Flat 2D top-down map (3D terrain disabled). maxPitch kept so the user
      // can still manually tilt if they want, but defaults to flat.
      zoom: INITIAL_ZOOM, pitch: INITIAL_PITCH, bearing: INITIAL_BEARING, maxPitch: 85,
      attributionControl: false, antialias: true,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');

    map.on('load', () => {
      loadedRef.current = true;

      // --- 3D terrain (disabled -> flat 2D map) ---
      if (ENABLE_TERRAIN) {
        try {
          map.setTerrain({ source: 'terrain-dem', exaggeration: TERRAIN_EXAGGERATION });
        } catch (e) {
          console.warn('[TacticalMap] setTerrain failed:', e);
        }
      } else {
        map.setTerrain(null); // ensure flat even if a previous style set terrain
      }

      // --- MGRS-style tactical grid (faint, under the AoE overlays) ---
      map.addSource('mgrs-grid', { type: 'geojson', data: gridGeoJSON(3000, 500) });
      map.addLayer({
        id: 'mgrs-grid', type: 'line', source: 'mgrs-grid',
        paint: { 'line-color': '#5fa8d3', 'line-width': 0.6, 'line-opacity': 0.22 },
      });

      // EW / jamming area (widest, purple)
      map.addSource('aa-ew', { type: 'geojson', data: EMPTY });
      map.addLayer({ id: 'aa-ew-fill', type: 'fill', source: 'aa-ew', paint: { 'fill-color': '#a855f7', 'fill-opacity': 0.1 } });
      map.addLayer({ id: 'aa-ew-line', type: 'line', source: 'aa-ew', paint: { 'line-color': '#a855f7', 'line-width': 1, 'line-opacity': 0.6, 'line-dasharray': [1, 2] } });

      // Radar detection range (dashed, colour = threat level)
      map.addSource('aa-radar', { type: 'geojson', data: EMPTY });
      map.addLayer({ id: 'aa-radar-fill', type: 'fill', source: 'aa-radar', paint: { 'fill-color': THREAT_COLOR.idle, 'fill-opacity': 0.14 } });
      map.addLayer({ id: 'aa-radar-line', type: 'line', source: 'aa-radar', paint: { 'line-color': THREAT_COLOR.idle, 'line-width': 1.5, 'line-opacity': 0.8, 'line-dasharray': [2, 2] } });

      // Engagement (lethal) range — solid semi-transparent inner circle
      map.addSource('aa-engage', { type: 'geojson', data: EMPTY });
      map.addLayer({ id: 'aa-engage-fill', type: 'fill', source: 'aa-engage', paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.22 } });
      map.addLayer({ id: 'aa-engage-line', type: 'line', source: 'aa-engage', paint: { 'line-color': '#ef4444', 'line-width': 1.5, 'line-opacity': 0.9 } });

      // Targeting line AA -> tracked/engaged drone
      map.addSource('aa-target-line', { type: 'geojson', data: EMPTY });
      map.addLayer({ id: 'aa-target-line', type: 'line', source: 'aa-target-line', paint: { 'line-color': '#ff4d4d', 'line-width': 2, 'line-opacity': 0.9, 'line-dasharray': [1, 1] } });

      overlaysRef.current = true;
      applyBasemap(map, basemapRef.current);
    });

    // C2: click the map while a placement tool is active -> place at lng/lat
    map.on('click', (e) => {
      if (toolRef.current !== 'off' && toolRef.current !== 'remove' && onClickRef.current) {
        onClickRef.current({ lng: e.lngLat.lng, lat: e.lngLat.lat, tool: toolRef.current });
      }
    });
    map.on('mousemove', () => {
      const canvas = map.getCanvas();
      canvas.style.cursor = toolRef.current !== 'off' ? 'crosshair' : '';
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      loadedRef.current = false;
      overlaysRef.current = false;
      markersRef.current = {};
      aaPopupRef.current = null;
      friendlyMarkerRef.current = null;
    };
  }, []);

  // --- basemap switch ---------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (loadedRef.current) applyBasemap(map, basemap);
    else map.once('load', () => applyBasemap(map, basemap));
  }, [basemap]);

  // --- imperative overlay + marker update on every telemetry tick -------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !telemetry || !telemetry.agents) return;

    const apply = () => {
      const agents = telemetry.agents;
      const aa = telemetry.aa;

      // ---- Friendly base (아군 진영) marker ----
      if (telemetry.friendly) {
        if (!friendlyMarkerRef.current) {
          const el = document.createElement('div');
          el.className = 'dx-unit friendly-hq';
          el.innerHTML =
            '<div class="dx-shape"></div>' +
            '<div class="dx-name">아군 진영 · BASE</div>' +
            '<div class="dx-equip">FRIENDLY HQ</div>';
          friendlyMarkerRef.current = new maplibregl.Marker({ element: el, anchor: 'center' })
            .setLngLat(localToLngLat(telemetry.friendly))
            .addTo(map);
        }
        friendlyMarkerRef.current.setLngLat(localToLngLat(telemetry.friendly));
      }

      // ---- AA AoE overlays ----
      if (aa && overlaysRef.current) {
        const color = THREAT_COLOR[aa.threat_level] || THREAT_COLOR.idle;
        map.getSource('aa-ew')?.setData({ type: 'FeatureCollection', features: [threatCircleGeoJSON(aa.position, aa.ew_range)] });
        map.getSource('aa-radar')?.setData({ type: 'FeatureCollection', features: [threatCircleGeoJSON(aa.position, aa.radar_range)] });
        map.getSource('aa-engage')?.setData({ type: 'FeatureCollection', features: [threatCircleGeoJSON(aa.position, aa.engagement_range)] });
        // dynamic threat colour on the radar ring
        map.setPaintProperty('aa-radar-line', 'line-color', color);
        map.setPaintProperty('aa-radar-fill', 'fill-color', color);

        // targeting line AA -> engaged (or first tracked) drone
        const targetId = aa.engaged || (aa.tracked && aa.tracked[0]);
        const tgt = targetId ? agents.find((a) => a.id === targetId) : null;
        map.getSource('aa-target-line')?.setData(
          tgt ? { type: 'FeatureCollection', features: [lineGeoJSON(aa.position, tgt.pos)] } : EMPTY
        );

        // Enemy strongpoint = red DIAMOND unit symbol + label (MIL-STD hostile).
        if (!aaPopupRef.current) {
          const el = document.createElement('div');
          el.className = 'dx-unit hostile';
          el.innerHTML =
            '<div class="dx-shape"></div>' +
            '<div class="dx-name">적 진지 · ENEMY AA</div>' +
            '<div class="dx-equip"></div>';
          aaPopupRef.current = new maplibregl.Marker({ element: el, anchor: 'center' })
            .setLngLat(localToLngLat(aa.position))
            .addTo(map);
        }
        aaPopupRef.current.setLngLat(localToLngLat(aa.position));
        aaPopupRef.current.getElement().querySelector('.dx-equip').textContent =
          `AMMO ${aa.ammo}/${aa.max_ammo} · ${(aa.threat_level || 'idle').toUpperCase()}`;
      }

      // ---- drone markers ----
      const seen = new Set();
      for (const a of agents) {
        seen.add(a.id);
        const lnglat = localToLngLat(a.pos);
        let m = markersRef.current[a.id];
        if (!m) {
          const { el, name, equip } = makeUnitEl();
          el.addEventListener('click', (ev) => {
            ev.stopPropagation();
            if (toolRef.current === 'remove' && onRemoveRef.current) onRemoveRef.current(a.id);
          });
          const marker = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(lnglat).addTo(map);
          m = { marker, el, name, equip };
          markersRef.current[a.id] = m;
        } else {
          m.marker.setLngLat(lnglat);
        }
        // our drones are FRIENDLY (blue rectangle); lost -> grey; staged -> dashed
        const cls = a.lost ? 'lost' : 'friendly';
        const staged = a.state === 'staged' ? ' staged' : '';
        m.el.className = `dx-unit ${cls}${staged}`;
        m.name.textContent = a.lost ? `${a.role} ✖` : a.role + (staged ? ' ⏸' : '');
        m.equip.textContent = a.equipment || '';
      }
      // remove markers for agents no longer present (e.g. removed/dormant)
      for (const id of Object.keys(markersRef.current)) {
        if (!seen.has(id)) {
          markersRef.current[id].marker.remove();
          delete markersRef.current[id];
        }
      }
    };

    if (loadedRef.current) apply();
    else map.once('load', apply);
  }, [telemetry]);

  // placement cursor
  useEffect(() => {
    const map = mapRef.current;
    if (map && map.getCanvas()) map.getCanvas().style.cursor = buildTool !== 'off' ? 'crosshair' : '';
  }, [buildTool]);

  return (
    <div
      ref={containerRef}
      className={invert ? 'dx-invert' : undefined}
      style={{ position: 'absolute', inset: 0, background: '#0b0f14' }}
    />
  );
}

export default memo(TacticalMapImpl);
