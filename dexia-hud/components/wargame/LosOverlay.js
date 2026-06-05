// LosOverlay — imperative MapLibre overlay for sensor line-of-sight.
//
// Reads precomputed LOS results from world_snapshot.jsonl (one record per
// cycle, produced by loop_cli.py using physics3d.clear_los).
// HUD displays only — NO raycast re-implementation in JS.
//
// Visible only when snapshot.has_terrain === true.
// On flat scenarios (has_terrain=false, los=[]) the sources are cleared.
//
// Sources used (pre-registered by WargameMap):
//   'wg-los-clear'   → green lines, visible=true
//   'wg-los-blocked' → red dashed lines, visible=false (terrain occlusion)

import { useEffect } from 'react';
import { localToLngLat, WARGAME_SCALE } from '../../lib/geo';

const EMPTY = { type: 'FeatureCollection', features: [] };

function posOf(entities, id) {
  if (!entities) return null;
  const e = entities.find(e => e.id === id);
  return e ? e.pos : null;
}

function makeLine(posA, posB, anchor) {
  if (!posA || !posB) return null;
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: [
        localToLngLat(posA, anchor, WARGAME_SCALE),
        localToLngLat(posB, anchor, WARGAME_SCALE),
      ],
    },
  };
}

export default function LosOverlay({ map, snapshot, anchor }) {
  useEffect(() => {
    if (!map) return;

    // No terrain → clear and hide
    if (!snapshot || !snapshot.has_terrain || !snapshot.los?.length) {
      map.getSource('wg-los-clear')?.setData(EMPTY);
      map.getSource('wg-los-blocked')?.setData(EMPTY);
      return;
    }

    const anch = anchor || { lat: 48.4, lon: 37.5 };
    const clearFeats   = [];
    const blockedFeats = [];

    for (const entry of snapshot.los) {
      const obsPos = posOf(snapshot.blue, entry.observer);
      const tgtPos = posOf(snapshot.red,  entry.target);
      const line = makeLine(obsPos, tgtPos, anch);
      if (!line) continue;
      if (entry.visible) {
        clearFeats.push(line);
      } else if (entry.blocked_by_terrain) {
        blockedFeats.push(line);
      }
    }

    map.getSource('wg-los-clear')?.setData({
      type: 'FeatureCollection', features: clearFeats,
    });
    map.getSource('wg-los-blocked')?.setData({
      type: 'FeatureCollection', features: blockedFeats,
    });
  }, [map, snapshot, anchor]);

  return null;
}
