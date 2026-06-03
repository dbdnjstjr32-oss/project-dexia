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

/** Inverse: [lon, lat] -> local sim meters [x, y]. */
export function lngLatToLocal(lon, lat, anchor = GEO_ANCHOR, scale = WORLD_SCALE) {
  const north = (lat - anchor.lat) * M_PER_DEG_LAT;
  const east = (lon - anchor.lon) * metersPerDegLon(anchor.lat);
  return [east / scale, north / scale];
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
