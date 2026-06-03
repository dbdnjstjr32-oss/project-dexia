# Dexia HUD — Tactical Ground Control Station (MapLibre GL)

A defense-grade GCS dashboard: a full-screen dark tactical map with live drone
markers, an Anti-Air threat radius, and two overlay panels driven by the Dexia
MARL telemetry stream (`telemetry.json`).

- **Base map** — full-screen **MapLibre GL** with a **basemap switcher** (top
  bar): **위성 / Satellite** (Esri World Imagery — real satellite terrain),
  **하이브리드 / Hybrid** (satellite + place/boundary labels), and **전술 /
  Dark** (CARTO Dark Matter tactical raster). All key-free. Switching only flips
  layer visibility (`setLayoutProperty`) — no style reload, so markers and the
  AA threat layers are never torn down. Default = satellite.
- **Markers** — 2 Recon (pulsing blue) + 4 Kamikaze (pulsing red); lost drones
  go grey with an ✖. Positions update imperatively every poll.
- **AA threat** — semi-transparent dashed radius rendered from the battery
  coordinates + radar range.
- **Left panel** — `SWARM TELEMETRY` (per-agent ID, role, altitude, speed,
  comms SNR, link) + `TARGET ACQUISITION` (unmasks the real target coordinates
  once the `broadcast` event fires).
- **Right panel** — `TACTICAL ADVISORY · AI STAFF` (routed from `lib/mockRag.js`)
  + `SYSTEM TELEMETRY` gauges (Network Survivability %, Wind Gust N, Attrition).

## A note on `mapcn`

`mapcn` is a shadcn-style component set that **wraps MapLibre GL** (react-map-gl).
To avoid depending on an API surface that may drift, this build targets the
**underlying `maplibre-gl` engine that `mapcn` wraps** directly. The data
pipeline (`lib/geo.js`, `lib/useTelemetry.js`, the telemetry feed) is unchanged
if you later swap in `mapcn`'s `<Map>` / `<MapMarker>` components — replace only
the imperative marker block in `components/TacticalMap.js`.

## Run it (two terminals)

**Terminal 1 — telemetry stream** (writes `../telemetry.json`):

```powershell
cd "C:\Users\dbdnj\Desktop\Project Dexia"
.\.venv312\Scripts\python.exe telemetry_stream.py --hz 10
```

**Terminal 2 — GCS dashboard:**

```powershell
cd "C:\Users\dbdnj\Desktop\Project Dexia\dexia-hud"
npm install        # first time only (installs maplibre-gl)
npm run dev
```

Open **http://localhost:3000**. Override the telemetry path if needed:
`$env:TELEMETRY_PATH = "C:\...\telemetry.json"; npm run dev`.

## Architecture & performance

```
DroneMARLEnv (AA + wind) ─► telemetry_stream.py ─► telemetry.json
                                                      │  poll 200 ms (useTelemetry)
                                              pages/index.js (Dashboard)
                            ┌──────────────────────────┼───────────────────────────┐
                   components/TacticalMap.js     LeftPanel (memo)          RightPanel (memo)
                   (MapLibre GL, memoized)       SWARM TELEMETRY           AI STAFF + gauges
                   imperative marker/threat      TARGET ACQUISITION        lib/mockRag.js
                   updates via refs
```

**Render optimizations (for high-FPS on Core Ultra 9 / 320 Hz):**

- The MapLibre map is created **once** (mount-only `useEffect`); React never
  re-creates or reconciles the WebGL canvas.
- Telemetry updates move markers and the AA radius **imperatively**
  (`Marker.setLngLat`, `GeoJSONSource.setData`) — GPU-composited, no React diff
  of map internals.
- `TacticalMap` is wrapped in `React.memo`; on each poll only its lightweight
  effect runs. Panels are independent `React.memo` components, so text updates
  never touch the map.
- Polling is decoupled from rendering in `lib/useTelemetry.js`, so the stream
  rate and the map FPS scale independently.

## Coordinate handling

The sim runs in a local meters frame (~15 m arena). `lib/geo.js` projects local
`[x, y]` to WGS84 lon/lat around a configurable `GEO_ANCHOR`, with a
`WORLD_SCALE` display-inflation factor so the small arena is legible at city
zoom. **For the production Tandem Tiltrotor VTOL GCS:** set `WORLD_SCALE = 1` and
point `GEO_ANCHOR` at the live launch fix — nothing else changes.
