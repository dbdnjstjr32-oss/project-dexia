import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

const M2DEG = 1 / 111000;

export default function LiveMap({ gameState }) {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const flewTo = useRef(false);

  useEffect(() => {
    if (map.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
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
    });

    map.current.on('load', () => {
      // Fog of war: the unconfirmed enemy area (drawn first, under the units)
      map.current.addSource('fog', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.current.addLayer({
        id: 'fog-fill', type: 'fill', source: 'fog',
        paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.16 },
      });
      map.current.addLayer({
        id: 'fog-line', type: 'line', source: 'fog',
        paint: { 'line-color': '#f59e0b', 'line-width': 1.5, 'line-dasharray': [3, 2], 'line-opacity': 0.6 },
      });

      map.current.addSource('blue-units', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.current.addLayer({
        id: 'blue-layer', type: 'circle', source: 'blue-units',
        paint: { 'circle-radius': 6, 'circle-color': '#3b82f6', 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' },
      });

      map.current.addSource('red-tracks', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.current.addLayer({
        id: 'red-uncertainty', type: 'circle', source: 'red-tracks',
        paint: {
          'circle-radius': ['max', 5, ['/', ['get', 'unc'], 25]],
          'circle-color': '#ef4444', 'circle-opacity': 0.12,
        },
      });
      map.current.addLayer({
        id: 'red-layer', type: 'circle', source: 'red-tracks',
        paint: { 'circle-radius': 6, 'circle-color': '#ef4444', 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' },
      });
    });

    return () => {
      if (map.current) map.current.remove();
      map.current = null;
      flewTo.current = false;
    };
  }, []);

  useEffect(() => {
    if (!map.current || !gameState || !map.current.isStyleLoaded()) return;

    const origin = (gameState.battlefield && gameState.battlefield.location) || [127.0, 38.0];
    const [originLon, originLat] = origin;
    const toLngLat = (x, y) => [originLon + M2DEG * (x || 0), originLat + M2DEG * (y || 0)];

    const blueFeatures = gameState.blue_details.map((b) => ({
      type: 'Feature', properties: { id: b.id, cls: b.cls },
      geometry: { type: 'Point', coordinates: toLngLat(b.pos[0], b.pos[1]) },
    }));
    const redFeatures = gameState.tracks.map((t) => ({
      type: 'Feature', properties: { id: t.track_id, conf: t.confidence, unc: t.uncertainty_r || 50 },
      geometry: { type: 'Point', coordinates: toLngLat(t.position[0], t.position[1]) },
    }));

    map.current.getSource('blue-units').setData({ type: 'FeatureCollection', features: blueFeatures });
    map.current.getSource('red-tracks').setData({ type: 'FeatureCollection', features: redFeatures });

    // fog polygon over the enemy band (blurred until recon reveals tracks on top)
    const ea = gameState.enemy_area;
    const fogFeatures = ea ? [{
      type: 'Feature', properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [[
          toLngLat(ea.x0, ea.y0), toLngLat(ea.x1, ea.y0),
          toLngLat(ea.x1, ea.y1), toLngLat(ea.x0, ea.y1), toLngLat(ea.x0, ea.y0),
        ]],
      },
    }] : [];
    map.current.getSource('fog').setData({ type: 'FeatureCollection', features: fogFeatures });

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
    </div>
  );
}
