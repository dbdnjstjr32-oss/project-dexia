// TrajectoryLayer — imperative MapLibre overlay for ballistic/missile arcs.
//
// Reads cycle.events[].trajectory (non-empty only on 3D terrain scenarios,
// produced by BallisticEngine.trajectory in dexia/fusion/effects.py).
//
// Each trajectory is a list of [x, y, z] points in sim metres.
// We draw:
//   • the [x,y] polyline projected via localToLngLat (scale=1)
//   • a launch-point marker (orange circle)
//   • an impact-point marker (red X / explosion icon)
//
// Source used (pre-registered by WargameMap): 'wg-trajectories'
// Launch/impact are DOM markers (maplibregl.Marker) — cleared each cycle.

import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import { localToLngLat, WARGAME_SCALE } from '../../lib/geo';

const EMPTY = { type: 'FeatureCollection', features: [] };

function makeLaunchEl() {
  const el = document.createElement('div');
  el.style.cssText = `
    width: 12px; height: 12px; border-radius: 50%;
    background: #f97316; border: 2px solid #fff;
    box-shadow: 0 0 8px #f9731688;
  `;
  return el;
}

function makeImpactEl(killed) {
  const el = document.createElement('div');
  el.style.cssText = `
    font-size: 18px; line-height: 1; cursor: default;
    filter: drop-shadow(0 0 4px #ef444488);
  `;
  el.textContent = killed?.length > 0 ? '💥' : '✕';
  return el;
}

export default function TrajectoryLayer({ map, cycle, anchor }) {
  const markerRefs = useRef([]);

  useEffect(() => {
    if (!map) return;

    // Remove previous markers
    markerRefs.current.forEach(m => m.remove());
    markerRefs.current = [];

    const events = cycle?.events || [];
    const trajs = events.filter(e => e.trajectory?.length > 1);

    if (trajs.length === 0) {
      map.getSource('wg-trajectories')?.setData(EMPTY);
      return;
    }

    const anch = anchor || { lat: 48.4, lon: 37.5 };
    const features = [];

    for (const ev of trajs) {
      const pts = ev.trajectory; // [[x,y,z], ...]
      const coords = pts.map(p => localToLngLat(p, anch, WARGAME_SCALE));

      features.push({
        type: 'Feature',
        properties: { asset: ev.asset_id },
        geometry: { type: 'LineString', coordinates: coords },
      });

      // Launch marker (first point)
      const launchLL = localToLngLat(pts[0], anch, WARGAME_SCALE);
      const launchM = new maplibregl.Marker({ element: makeLaunchEl(), anchor: 'center' })
        .setLngLat(launchLL)
        .addTo(map);
      markerRefs.current.push(launchM);

      // Impact marker (last point)
      const impactLL = localToLngLat(pts[pts.length - 1], anch, WARGAME_SCALE);
      const impactM = new maplibregl.Marker({
        element: makeImpactEl(ev.killed),
        anchor: 'center',
      }).setLngLat(impactLL).addTo(map);
      markerRefs.current.push(impactM);
    }

    map.getSource('wg-trajectories')?.setData({
      type: 'FeatureCollection', features,
    });
  }, [map, cycle, anchor]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      markerRefs.current.forEach(m => m.remove());
      markerRefs.current = [];
    };
  }, []);

  return null;
}
