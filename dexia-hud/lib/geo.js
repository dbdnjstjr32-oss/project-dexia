// Geo projection: Dexia local simulation frame (meters) -> WGS84 lon/lat.
//
// The MARL sim runs in a small local ENU-style frame (x = east m, y = north m,
// z = up m), with the arena spanning only ~15 m. A real GCS map needs geographic
// coordinates, so we anchor the local origin to a configurable lat/lon and apply
// an equirectangular (flat-earth) approximation — exact enough for a few hundred
// meters. A WORLD_SCALE factor inflates the tiny arena so it is legible at city
// map zoom; set it to 1 for a real-world VTOL flying true GPS distances.
//
// For the production Tandem Tiltrotor GCS, swap ANCHOR for the live home/launch
// fix and set WORLD_SCALE = 1 — the rest of the pipeline is unchanged.

export const GEO_ANCHOR = { lat: 37.5665, lon: 126.978 }; // GCS home fix (placeholder)
export const WORLD_SCALE = 8; // display inflation of the ~15 m sim arena
// Zoomed in so the small play area fills the screen -> map clicks land on
// sensible local coords (±~15 m) instead of kilometres off-area.
export const INITIAL_ZOOM = 18.5;

// ---- Wargame HUD constants (km-scale, ±8000 m sim frame) ----------------
// Wargame positions are TRUE metres (ENU), so scale=1 projects them directly.
// Zoom ~12-13 shows a ~20 km battlefield at a legible size.
export const WARGAME_SCALE = 1;
export const WARGAME_ZOOM  = 12.5;

// Per-theater geographic anchors: the sim-frame origin [0,0] maps to this
// real-world lat/lon.  Exact cartographic accuracy is not required — the map
// is used for tactical display, not navigation.
export const THEATER_ANCHORS = {
  eastern_europe: { lat: 48.40, lon: 37.50 }, // Donbas region
  ukraine:        { lat: 48.40, lon: 37.50 },
  ua_east:        { lat: 48.40, lon: 37.50 },
  korea:          { lat: 37.90, lon: 127.10 }, // central Korea
  default:        { lat: 48.40, lon: 37.50 },
};

/** Return the best geographic anchor for a given theater string. */
export function theaterAnchor(theater) {
  const key = (theater || '').toLowerCase().replace(/-/g, '_');
  return THEATER_ANCHORS[key] || THEATER_ANCHORS.default;
}

// Default wargame anchor (used before a scenario is selected)
export const WARGAME_ANCHOR = THEATER_ANCHORS.default;

const M_PER_DEG_LAT = 111320;

function metersPerDegLon(lat) {
  return M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);
}

/** [x, y, z?] local meters -> [lon, lat]. */
export function localToLngLat(pos, anchor = GEO_ANCHOR, scale = WORLD_SCALE) {
  const east = pos[0] * scale;
  const north = pos[1] * scale;
  const lat = anchor.lat + north / M_PER_DEG_LAT;
  const lon = anchor.lon + east / metersPerDegLon(anchor.lat);
  return [lon, lat];
}

/** MGRS-style tactical grid: square line grid in real metres around the anchor.
 *  Decoupled from WORLD_SCALE so it maps to true ground distances.
 *  extentM = half-size of the covered area; stepM = cell size. */
export function gridGeoJSON(extentM = 3000, stepM = 500, anchor = GEO_ANCHOR) {
  const mLat = M_PER_DEG_LAT;
  const mLon = metersPerDegLon(anchor.lat);
  const toLL = (e, n) => [anchor.lon + e / mLon, anchor.lat + n / mLat];
  const line = (coords) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } });
  const feats = [];
  for (let e = -extentM; e <= extentM + 1e-6; e += stepM) {
    feats.push(line([toLL(e, -extentM), toLL(e, extentM)]));
  }
  for (let n = -extentM; n <= extentM + 1e-6; n += stepM) {
    feats.push(line([toLL(-extentM, n), toLL(extentM, n)]));
  }
  return { type: 'FeatureCollection', features: feats };
}

/** Tactical grid in the SIM frame (so cells = sim metres, consistent with unit
 *  positions and threat-range circles which also use WORLD_SCALE). Replaces the
 *  real-world MGRS grid whose true-metre spacing didn't match the inflated units.
 *  halfLocal/stepLocal are LOCAL sim metres. */
export function simGridGeoJSON(halfLocal = 150, stepLocal = 10, anchor = GEO_ANCHOR, scale = WORLD_SCALE) {
  const line = (coords) => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } });
  const feats = [];
  for (let e = -halfLocal; e <= halfLocal + 1e-6; e += stepLocal) {
    feats.push(line([localToLngLat([e, -halfLocal], anchor, scale), localToLngLat([e, halfLocal], anchor, scale)]));
  }
  for (let n = -halfLocal; n <= halfLocal + 1e-6; n += stepLocal) {
    feats.push(line([localToLngLat([-halfLocal, n], anchor, scale), localToLngLat([halfLocal, n], anchor, scale)]));
  }
  return { type: 'FeatureCollection', features: feats };
}

/** Inverse: [lon, lat] -> local sim meters [x, y]. */
export function lngLatToLocal(lon, lat, anchor = GEO_ANCHOR, scale = WORLD_SCALE) {
  const north = (lat - anchor.lat) * M_PER_DEG_LAT;
  const east = (lon - anchor.lon) * metersPerDegLon(anchor.lat);
  return [east / scale, north / scale];
}

// Half-size (in LOCAL sim metres) of the placeable arena. Keeps clicks/placements
// bounded so units never end up kilometres off-screen. Display metres = ×WORLD_SCALE.
export const ARENA_HALF_M = 120;

/** Clamp a local [x, y] into the placeable arena (or a table's centre±extent). */
export function clampLocal([x, y], center = [0, 0], halfM = ARENA_HALF_M) {
  const cx = center[0] || 0, cy = center[1] || 0;
  return [
    Math.min(Math.max(x, cx - halfM), cx + halfM),
    Math.min(Math.max(y, cy - halfM), cy + halfM),
  ];
}

/** maxBounds [[W,S],[E,N]] around the anchor — constrains map panning so clicks
 *  stay near the small sim arena (display metres). */
export function anchorBounds(halfDisplayM = 1600, anchor = GEO_ANCHOR) {
  const dLat = halfDisplayM / M_PER_DEG_LAT;
  const dLon = halfDisplayM / metersPerDegLon(anchor.lat);
  return [
    [anchor.lon - dLon, anchor.lat - dLat],
    [anchor.lon + dLon, anchor.lat + dLat],
  ];
}

/** Square battlefield-table area polygon: center local [x,y] ± extentM (sim m). */
export function rectGeoJSON(center, extentM, anchor = GEO_ANCHOR, scale = WORLD_SCALE) {
  const [cx, cy] = center;
  const e = Math.abs(extentM);
  const corners = [
    [cx - e, cy - e], [cx + e, cy - e], [cx + e, cy + e], [cx - e, cy + e], [cx - e, cy - e],
  ].map((p) => localToLngLat(p, anchor, scale));
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [corners] } };
}

/** GeoJSON LineString between two local points (sim meters). */
export function lineGeoJSON(localA, localB) {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [localToLngLat(localA), localToLngLat(localB)] },
    properties: {},
  };
}

/** Directional FOV cone (sector) polygon for directional weapons.
 *  center: local [x,y]; bearingDeg: cone centre heading (0=+x); halfAngleDeg;
 *  rangeM: cone range in sim metres. */
export function directionalConeGeoJSON(
  center, bearingDeg, halfAngleDeg, rangeM, anchor = GEO_ANCHOR, scale = WORLD_SCALE, steps = 24
) {
  const b = (bearingDeg * Math.PI) / 180;
  const h = (halfAngleDeg * Math.PI) / 180;
  const pts = [[center[0], center[1]]];
  for (let i = 0; i <= steps; i++) {
    const a = b - h + (2 * h * i) / steps;
    pts.push([center[0] + rangeM * Math.cos(a), center[1] + rangeM * Math.sin(a)]);
  }
  pts.push([center[0], center[1]]);
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [pts.map((p) => localToLngLat(p))] },
    properties: {},
  };
}

/** GeoJSON polygon approximating a circle (threat radius) around a local point. */
export function threatCircleGeoJSON(
  centerPos,
  radiusM,
  anchor = GEO_ANCHOR,
  scale = WORLD_SCALE,
  steps = 72
) {
  const r = radiusM * scale;
  const mLat = M_PER_DEG_LAT;
  const mLon = metersPerDegLon(anchor.lat);
  const cx = centerPos[0] * scale;
  const cy = centerPos[1] * scale;
  const coords = [];
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI;
    const dx = r * Math.cos(a);
    const dy = r * Math.sin(a);
    coords.push([anchor.lon + (cx + dx) / mLon, anchor.lat + (cy + dy) / mLat]);
  }
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [coords] },
    properties: {},
  };
}
