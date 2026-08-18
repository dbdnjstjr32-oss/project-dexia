// TrackLayer — imperative MapLibre overlay for fused enemy tracks.
//
// Renders cycle.fusion[] as:
//   • a Marker per track (colored div element, MIL-STD hostile diamond shape)
//   • an uncertainty ring (threatCircleGeoJSON via source 'wg-tracks-ring')
//   • coasting tracks get a dashed ring ('wg-tracks-coast') and lower opacity
//
// Confidence color scale:
//   0.0–0.4  →  #ef4444 (red, SIGINT-only vague track)
//   0.4–0.7  →  #f59e0b (amber, partially confirmed)
//   0.7–1.0  →  #22c55e (green, multi-source confirmed)
//
// All map updates are IMPERATIVE (setData, Marker.setLngLat) — no React
// re-render of the WebGL canvas on every cycle scrub.

import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import { localToLngLat, threatCircleGeoJSON, WARGAME_SCALE } from '../../lib/geo';

const EMPTY = { type: 'FeatureCollection', features: [] };

function confColor(conf) {
  if (conf >= 0.7) return '#22c55e';
  if (conf >= 0.4) return '#f59e0b';
  return '#ef4444';
}

function confOpacity(conf, status) {
  if (status === 'coasting') return 0.4;
  if (status === 'stale')    return 0;
  return 0.5 + conf * 0.4;  // 0.5 → 0.9
}

// Category → abbreviated label for the marker element
const CAT_LABEL = {
  armor:       'ARM', apc: 'APC', infantry: 'INF', artillery: 'ART',
  air_defense: 'AD',  ew: 'EW',  emitter: 'EMT',  air: 'AIR',
};

function makeTrackEl(track) {
  const color = confColor(track.confidence);
  const opacity = track.status === 'coasting' ? 0.5 : 1;
  const el = document.createElement('div');
  el.className = 'wg-track-marker';
  el.style.cssText = `
    position: relative; width: 0; height: 0;
    opacity: ${opacity};
    transition: opacity 0.3s ease;
    cursor: pointer;
  `;

  // Hostile diamond shape
  const diamond = document.createElement('div');
  diamond.style.cssText = `
    position: absolute; width: 18px; height: 18px;
    left: 50%; top: 50%;
    transform: translate(-50%, -50%) rotate(45deg);
    background: ${color}33;
    border: 2px solid ${color};
    box-shadow: 0 0 10px ${color}88;
    transition: background 0.3s, border-color 0.3s;
  `;
  if (track.status === 'coasting') {
    diamond.style.borderStyle = 'dashed';
  }

  // Category label
  const label = document.createElement('div');
  label.style.cssText = `
    position: absolute; left: 50%; top: 14px;
    transform: translateX(-50%);
    white-space: nowrap; font: 700 9px/1.2 'Segoe UI', monospace;
    color: ${color}; text-shadow: 0 0 4px #000, 0 0 4px #000;
    letter-spacing: 0.5px;
  `;
  label.textContent = CAT_LABEL[track.category] || track.category?.slice(0,3).toUpperCase() || '?';

  // Track ID sub-label
  const idLabel = document.createElement('div');
  idLabel.style.cssText = `
    position: absolute; left: 50%; top: 23px;
    transform: translateX(-50%);
    white-space: nowrap; font: 600 8px/1.2 'Segoe UI', monospace;
    color: #8899aa; text-shadow: 0 0 4px #000;
  `;
  idLabel.textContent = track.track_id;

  el.appendChild(diamond);
  el.appendChild(label);
  el.appendChild(idLabel);
  return { el, diamond, label };
}

function buildRingFeatures(tracks, anchor) {
  const active  = [];
  const coasting = [];
  for (const t of tracks) {
    if (t.status === 'stale') continue;
    const color = confColor(t.confidence);
    const feat = threatCircleGeoJSON(t.position, t.uncertainty_r, anchor, WARGAME_SCALE);
    feat.properties = { color, opacity: t.status === 'coasting' ? 0.25 : 0.7 };
    if (t.status === 'coasting') coasting.push(feat);
    else                         active.push(feat);
  }
  return { active, coasting };
}

function buildPopupHtml(t) {
  const color = confColor(t.confidence);
  const confPct = Math.round(t.confidence * 100);
  const sources = (t.sources || []).join(', ');
  const vel = t.velocity ? `${t.velocity[0].toFixed(1)}, ${t.velocity[1].toFixed(1)}` : '—';
  return `
    <div style="font:12px/1.5 'Segoe UI',monospace;color:#cfe3ff;min-width:160px">
      <div style="font-weight:800;color:${color};margin-bottom:4px;letter-spacing:0.5px">
        ${t.track_id}
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 10px;font-size:11px">
        <span style="color:#8899aa">Category</span><span>${t.category}</span>
        <span style="color:#8899aa">Confidence</span>
        <span style="color:${color};font-weight:700">${confPct}%</span>
        <span style="color:#8899aa">Status</span><span>${t.status}</span>
        <span style="color:#8899aa">Sources</span><span>${sources || '—'}</span>
        <span style="color:#8899aa">Vel (m/s)</span><span>[${vel}]</span>
        <span style="color:#8899aa">Uncertainty</span><span>±${t.uncertainty_r} m</span>
      </div>
      ${t.sources?.length > 1
        ? `<div style="margin-top:6px;font-size:10px;color:#52c41a">✓ 다중 센서 확인됨</div>`
        : `<div style="margin-top:6px;font-size:10px;color:#faad14">⚠ 단일 센서 — 불확실</div>`}
    </div>
  `;
}

export default function TrackLayer({ map, cycle, anchor }) {
  const markersRef = useRef({});  // track_id → { marker, popup, el, diamond }

  useEffect(() => {
    if (!map || !cycle) return;

    const tracks = cycle.fusion || [];
    const anch = anchor || { lat: 48.4, lon: 37.5 };
    const seen = new Set();

    // --- Update / create markers ---
    for (const t of tracks) {
      if (t.status === 'stale') continue;
      seen.add(t.track_id);
      const lnglat = localToLngLat(t.position, anch, WARGAME_SCALE);

      let m = markersRef.current[t.track_id];
      if (!m) {
        const { el, diamond, label } = makeTrackEl(t);
        const popup = new maplibregl.Popup({
          closeButton: false, closeOnClick: false,
          className: 'wg-track-popup',
          offset: [0, -12],
        }).setHTML(buildPopupHtml(t));

        el.addEventListener('mouseenter', () => popup.addTo(map));
        el.addEventListener('mouseleave', () => popup.remove());

        const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
          .setLngLat(lnglat)
          .addTo(map);

        m = { marker, popup, el, diamond, label };
        markersRef.current[t.track_id] = m;
      } else {
        m.marker.setLngLat(lnglat);
        // Update popup content
        m.popup.setHTML(buildPopupHtml(t));
        // Update diamond color / style
        const color = confColor(t.confidence);
        m.diamond.style.background = `${color}33`;
        m.diamond.style.borderColor = color;
        m.diamond.style.borderStyle = t.status === 'coasting' ? 'dashed' : 'solid';
        m.diamond.style.boxShadow = `0 0 10px ${color}88`;
        m.el.style.opacity = t.status === 'coasting' ? '0.5' : '1';
      }
    }

    // Remove stale / gone markers
    for (const [id, m] of Object.entries(markersRef.current)) {
      if (!seen.has(id)) {
        m.marker.remove();
        m.popup.remove();
        delete markersRef.current[id];
      }
    }

    // --- Update uncertainty rings (GeoJSON sources) ---
    const { active, coasting } = buildRingFeatures(tracks, anch);
    map.getSource('wg-tracks-ring')?.setData({
      type: 'FeatureCollection', features: active,
    });
    map.getSource('wg-tracks-coast')?.setData({
      type: 'FeatureCollection', features: coasting,
    });
  }, [map, cycle, anchor]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      for (const m of Object.values(markersRef.current)) {
        m.marker.remove();
        m.popup.remove();
      }
      markersRef.current = {};
    };
  }, []);

  return null; // purely imperative — no DOM output
}
