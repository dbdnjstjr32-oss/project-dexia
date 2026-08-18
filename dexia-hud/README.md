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

---

## Wargame HUD (Build #8) — AI 추론 관측 레이어

`http://localhost:3000/wargame` 에 마운트된 전술 워게임 재생 플레이어.
AIP 시뮬 코어(Builds 1–6)가 생성한 JSONL 파일을 읽어 AI의 *왜 그 결정을 했는지*를 시각화한다.

### 구조

```
Python 시뮬 코어 (수정 불가)
  └─ MissionRunner.run()   → reasoning_trace.jsonl  (1행 = 1결정 사이클)
dexia/agent/loop_cli.py (Build #8 추가)
  └─ SnapshotRunner._emit() → world_snapshot.jsonl  (1행 = 1사이클, LOS 포함)

Next.js API 라우트 (pages/api/wargame/)
  ├─ GET  /scenarios  → scenarios/ 디렉토리 스캔
  ├─ GET  /trace      → reasoning_trace.jsonl 파싱
  ├─ GET  /snapshot   → world_snapshot.jsonl 파싱
  ├─ GET  /campaign   → scenario_evals.jsonl 파싱
  └─ POST /run        → loop_cli.py 스폰 → 완료 후 trace+snapshot 반환

React 컴포넌트 (components/wargame/)
  ├─ WargameMap        — MapLibre GL (scale=1, zoom≈12.5)
  ├─ TrackLayer        — 융합 적 유닛 마커 + 불확도 링
  ├─ LosOverlay        — LOS 사시선 (초록=가시/빨간점선=지형차단)
  ├─ TrajectoryLayer   — 탄도 포물선 (3D 시나리오)
  ├─ ReasoningTimeline — AI 결정 타임라인 (우측 레일)
  ├─ ScenarioPicker    — 시나리오 선택 + Run (좌측 레일)
  └─ CampaignScoreboard— 캠페인 KPI + 극장별 통계 (좌측 레일)
```

### 데이터 생성 (수동)

```powershell
# 레포 루트에서 — 단일 시나리오 실행
python -m dexia.agent.loop_cli --scenario ridge-los-p4
# → reasoning_trace.jsonl  (16 사이클)
# → world_snapshot.jsonl   (16 사이클, LOS 포함)

# 캠페인 전체 평가 (CampaignScoreboard용)
python -m dexia.agent.campaign --count 20
# → scenario_evals.jsonl
```

### HUD 실행

```powershell
cd dexia-hud
npm run dev
```

- **GCS 대시보드**: http://localhost:3000
- **워게임 HUD**: http://localhost:3000/wargame

### LOS 설계 원칙

**HUD는 표시 전용** — LOS는 Python `physics3d.clear_los(terrain, obs, tgt)`로 시뮬에서 미리 계산되어 `world_snapshot.jsonl`의 `los[]`에 기록됨. MapLibre에서 raycast를 재구현하지 않는다. 이는 다음을 보장한다:

1. **단일 진실원천**: Python P4 테스트와 HUD 표시가 동일한 LOS 결과
2. **성능**: N×M 레이캐스트가 매 프레임이 아닌 시뮬 시간에 한 번만 계산
3. **센서 확장성**: 새 센서가 추가되어도 HUD는 `visible` boolean만 읽으면 됨
