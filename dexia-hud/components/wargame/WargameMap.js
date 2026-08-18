// WargameMap — MapLibre GL base layer for the AIP wargame HUD.
//
// Intentionally separate from the drone GCS TacticalMap to avoid mixing
// coordinate systems (wargame = km-scale, scale=1 vs. drone = 15 m, scale=8).
// Follows exactly the same imperative MapLibre pattern:
//   - map created ONCE in a useEffect with []
//   - child overlays (TrackLayer, LosOverlay, TrajectoryLayer) receive the
//     map ref and update via source.setData() — no React re-render of canvas
//
// Props:
//   anchor    {lat, lon}   — theater geographic anchor (wargame origin)
//   basemap   string       — 'satellite' | 'hybrid' | 'topo' | 'dark'
//   onReady   fn(map)      — called once the map 'load' event fires

import { useEffect, useRef, memo } from 'react';
import maplibregl from 'maplibre-gl';
import { WARGAME_ZOOM, WARGAME_SCALE, localToLngLat } from '../../lib/geo';

const ESRI_SAT  = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const ESRI_REF  = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';
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

const EMPTY = { type: 'FeatureCollection', features: [] };

function visFor(bm) {
  return {
    'bm-sat':  bm === 'satellite' || bm === 'hybrid' ? 'visible' : 'none',
    'bm-ref':  bm === 'hybrid' ? 'visible' : 'none',
    'bm-topo': bm === 'topo' ? 'visible' : 'none',
    'bm-dark': bm === 'dark' ? 'visible' : 'none',
  };
}
function applyBasemap(map, bm) {
  for (const [id, v] of Object.entries(visFor(bm))) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
  }
}
function buildStyle(bm) {
  const v = visFor(bm);
  return {
    version: 8,
    sources: {
      'bm-sat':  { type: 'raster', tiles: [ESRI_SAT],  tileSize: 256, maxzoom: 19, attribution: 'Imagery © Esri' },
      'bm-ref':  { type: 'raster', tiles: [ESRI_REF],  tileSize: 256, maxzoom: 19 },
      'bm-topo': { type: 'raster', tiles: OPENTOPO,    tileSize: 256, maxzoom: 17, attribution: '© OpenTopoMap (CC-BY-SA)' },
      'bm-dark': { type: 'raster', tiles: CARTO_DARK,  tileSize: 256, attribution: '© OpenStreetMap © CARTO' },
    },
    layers: [
      { id: 'bm-sat',  type: 'raster', source: 'bm-sat',  layout: { visibility: v['bm-sat']  } },
      { id: 'bm-ref',  type: 'raster', source: 'bm-ref',  layout: { visibility: v['bm-ref']  } },
      { id: 'bm-topo', type: 'raster', source: 'bm-topo', layout: { visibility: v['bm-topo'] } },
      { id: 'bm-dark', type: 'raster', source: 'bm-dark', layout: { visibility: v['bm-dark'] } },
    ],
  };
}

// Wargame tactical grid: 1 km cells in sim metres
function wargameGridGeoJSON(anchor, halfM = 10000, stepM = 1000) {
  const M_PER_DEG_LAT = 111320;
  const mLon = M_PER_DEG_LAT * Math.cos((anchor.lat * Math.PI) / 180);
  const toLL = (e, n) => [anchor.lon + e / mLon, anchor.lat + n / M_PER_DEG_LAT];
  const line = (coords) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } });
  const feats = [];
  for (let e = -halfM; e <= halfM; e += stepM)
    feats.push(line([toLL(e, -halfM), toLL(e, halfM)]));
  for (let n = -halfM; n <= halfM; n += stepM)
    feats.push(line([toLL(-halfM, n), toLL(halfM, n)]));
  return { type: 'FeatureCollection', features: feats };
}

function WargameMapImpl({ anchor, basemap = 'dark', onReady }) {
  const containerRef = useRef(null);
  const mapRef       = useRef(null);
  const loadedRef    = useRef(false);
  const basemapRef   = useRef(basemap); basemapRef.current = basemap;
  const onReadyRef   = useRef(onReady); onReadyRef.current = onReady;

  // Create the map once
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;
    const anch = anchor || { lat: 48.4, lon: 37.5 };

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(basemapRef.current),
      center: [anch.lon, anch.lat],
      zoom: WARGAME_ZOOM,
      pitch: 0, bearing: 0,
      maxPitch: 60,
      attributionControl: false,
      antialias: true,
    });

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');

    map.on('load', () => {
      loadedRef.current = true;

      // Tactical 1-km grid
      map.addSource('wg-grid', { type: 'geojson', data: wargameGridGeoJSON(anch) });
      map.addLayer({
        id: 'wg-grid', type: 'line', source: 'wg-grid',
        paint: { 'line-color': '#4a7a9b', 'line-width': 0.5, 'line-opacity': 0.18 },
      });

      // ---- Overlay sources (all start empty; child components fill them) ----

      // TrackLayer: uncertainty rings + coasting rings
      map.addSource('wg-tracks-ring',     { type: 'geojson', data: EMPTY });
      map.addSource('wg-tracks-coast',    { type: 'geojson', data: EMPTY });

      map.addLayer({
        id: 'wg-tracks-ring', type: 'line', source: 'wg-tracks-ring',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 1.5,
          'line-opacity': ['get', 'opacity'],
        },
      });
      map.addLayer({
        id: 'wg-tracks-coast', type: 'line', source: 'wg-tracks-coast',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 1,
          'line-opacity': 0.35,
          'line-dasharray': [4, 3],
        },
      });

      // LosOverlay: clear sightlines (green) and blocked (red)
      map.addSource('wg-los-clear',   { type: 'geojson', data: EMPTY });
      map.addSource('wg-los-blocked', { type: 'geojson', data: EMPTY });

      map.addLayer({
        id: 'wg-los-clear', type: 'line', source: 'wg-los-clear',
        paint: { 'line-color': '#22c55e', 'line-width': 1.5, 'line-opacity': 0.5 },
      });
      map.addLayer({
        id: 'wg-los-blocked', type: 'line', source: 'wg-los-blocked',
        paint: { 'line-color': '#ef4444', 'line-width': 2, 'line-opacity': 0.8, 'line-dasharray': [3, 2] },
      });

      // TrajectoryLayer: ballistic arc polyline + launch/impact markers
      map.addSource('wg-trajectories', { type: 'geojson', data: EMPTY });
      map.addLayer({
        id: 'wg-trajectories', type: 'line', source: 'wg-trajectories',
        paint: {
          'line-color': '#f97316',
          'line-width': 2,
          'line-opacity': 0.85,
          'line-dasharray': [2, 1],
        },
      });

      applyBasemap(map, basemapRef.current);
      if (onReadyRef.current) onReadyRef.current(map);
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      loadedRef.current = false;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Basemap switch
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (loadedRef.current) applyBasemap(map, basemap);
    else map.once('load', () => applyBasemap(map, basemap));
  }, [basemap]);

  // Fly to new theater anchor
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !anchor) return;
    const fly = () => map.flyTo({ center: [anchor.lon, anchor.lat], zoom: WARGAME_ZOOM, duration: 1200 });
    if (loadedRef.current) fly();
    else map.once('load', fly);
  }, [anchor]);

  return (
    <div
      ref={containerRef}
      style={{ position: 'absolute', inset: 0, background: '#090d12' }}
    />
  );
}

export default memo(WargameMapImpl);
