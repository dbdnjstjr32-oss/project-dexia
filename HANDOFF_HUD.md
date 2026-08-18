# HANDOFF — Build #8: AIP Wargame HUD

> You are inheriting the **Dexia AIP wargame**. The Python simulation core (builds 1–6) and the
> tier-B 3D physics (P1/P3/P4/P2) are **done, tested, committed**. Your job is the **HUD (#8)**:
> a browser UI that visualizes *why the AI decided what it did*, the fused enemy picture, terrain
> line-of-sight, weapon trajectories, and campaign analytics. This document is everything you need
> — exact data shapes, file locations, conventions, the plan, and the acceptance bar. Read it fully
> before writing code.

---

## 0. TL;DR — what to build

Five React components + their data routes, mounted in the existing Next.js app at `dexia-hud/`:

| Component | Shows | Primary data |
|---|---|---|
| `ReasoningTimeline.js` | per-cycle decision timeline: collect→suppress→fires, with the AI's rationale | `reasoning_trace.jsonl` |
| `TrackLayer.js` (map) | fused enemy tracks on a map, colored by confidence + source provenance | `reasoning_trace.jsonl` → `fusion[]` |
| `LosOverlay.js` (map) | sensor→target sightlines, occluded (red) vs clear (green) over terrain | tracks + entity/sensor positions |
| `TrajectoryLayer.js` (map) | ballistic/missile arcs of fired weapons | `events[].trajectory` |
| `ScenarioPicker.js` | choose 1 of ~104 scenarios to load/replay | `scenarios/` listing |
| `CampaignScoreboard.js` | pass rate, scores, outcomes by theater | `scenario_evals.jsonl` |

**Acceptance:** load a scenario in the browser, press play, and watch one operation replay so that
"the AI saw a vague SIGINT cut → tasked ISR to confirm → jammed the SAM → massed fires on the armor"
is *visible*, with the fused tracks, the occluded-by-ridge target, and the shell arcs drawn on the map.

The core deliverable is **observability of the AI's reasoning**, not a pretty map. Prioritize the
ReasoningTimeline + TrackLayer; LOS/trajectory/scoreboard are the next ring.

---

## 1. Project orientation (read once)

Dexia models an autonomous tactical AI that runs a **kill chain** over a declarative wargame:

```
Catalog (equipment) + Scenario (mission)  ──► WorldState (ground truth, 3D)
        │                                          │ feeds observe imperfectly
        ▼                                          ▼
   AssetMatch (which asset can service a track) ── FusionEngine (tracks w/ confidence + sources)
        │                                          │
        ▼                                          ▼
   Policy.decide (collect-or-act) ──► ActionBus (governance funnel) ──► EffectResolver (real effects)
        │
        ▼
   reasoning_trace.jsonl  (one record per decision cycle — THE thing the HUD visualizes)
```

- **Everything is data-driven.** Equipment is `equipment_catalog.yaml`; missions are `scenarios/*.yaml`.
- **The AI is deterministic today** (no LLM in the loop — that's a later build). The trace is the
  honest record of its reasoning.
- **Physics is 3D and gated on terrain.** A scenario with a `terrain:` block runs real 3D motion +
  line-of-sight occlusion + ballistic arcs; without it, the legacy flat path runs unchanged. The HUD
  must handle both (2D flat scenarios AND 3D terrain scenarios).

You do **not** need to modify the Python core for the HUD. Treat it as a producer of data files /
endpoints. **Do not change `dexia/fusion`, `dexia/agent`, `dexia/scenario`, `dexia/physics3d`** — if
you think you need to, you've misunderstood the contract below; ask first.

---

## 2. Current state & how to run things

- Language/stack: Python 3.13 backend; `dexia-hud/` is **Next.js 14.2.5 (pages router)**, React 18.3.1,
  **maplibre-gl 4.7.1**. No Redux/state lib; no TypeScript (plain `.js`).
- Tests: `python -m pytest tests/ -q --ignore=tests/test_phase86_doctrine_enforcement.py` → **76 pass**.
  (`test_phase86` errors only because optional `ray` isn't installed — pre-existing, ignore it.)
- Physics demos: `python tests/test_physics_integration.py`, `python tests/test_physics3d_effects.py`,
  `python tests/test_jsbsim_engine.py` (each prints a readable verification).
- Campaign eval: `python -m dexia.agent.campaign --all` (writes `scenario_evals.jsonl`).

### Generating the data the HUD reads

**A single mission trace → `reasoning_trace.jsonl`** (repo root; constant `DEFAULT_TRACE_PATH` in
`dexia/agent/loop.py`):

```python
# scripts/run_trace.py  (you may add this helper)
from dexia.scenario.scenario import load_scenario
from dexia.scenario.catalog import load_catalog
from dexia.agent.loop import MissionRunner
sc = load_scenario("ridge-los-p4")          # any id under scenarios/
MissionRunner(sc, load_catalog()).run()     # writes ./reasoning_trace.jsonl (fresh each run)
```

**Campaign scores → `scenario_evals.jsonl`** (repo root; `DEFAULT_RESULTS` in
`dexia/agent/campaign.py`): `python -m dexia.agent.campaign --all`.

**Scenario library:** `scenarios/generated/*.yaml` (100, the eval set) + `scenarios/*.yaml`
(`ua-east-armor-thrust-007` seed, `ridge-assault-3d`, `ridge-los-p4` 3D demos).

---

## 3. DATA CONTRACTS — exact shapes (this is the part you cannot get wrong)

### 3.1 `reasoning_trace.jsonl` — one JSON object per line = one decision cycle
Source of truth: `DecisionRecord.to_dict` in `dexia/agent/loop.py`.

```jsonc
{
  "cycle": 0,                       // 0-based cycle index
  "tick": 12,                       // sim tick at end of cycle (12 ticks/cycle default)
  "intent": "delay",               // deny | destroy | delay | recon | seize
  "perceive": { "feeds": ["sigint","uav_eo"], "tracks": 3 },   // which feeds reported; live track count
  "fusion":  [ Track, ... ],        // see 3.2 — the fused enemy picture THIS cycle
  "gaps":    [ { "track":"TRK-EMI-001", "category":"emitter", "conf":0.4, "why":"conf < 0.60 — 표적 불확실" } ],
  "asset_match": { "TRK-ARM-002": [ Option, ... ] },           // see 3.4 — top-3 options per track
  "reasoning": "[collect] TRK-EMI-001 ... / [suppress] ...",  // human-readable cycle summary
  "decisions":  [ Decision, ... ],  // see 3.3 — what the AI chose to DO
  "governance": [ { "cmd":"jam", "asset":"ew_jammer_gnd", "status":"accepted", "reason":null } ],
  "events":     [ EffectEvent, ... ] // see 3.5 — effects that fired/landed this cycle
}
```

### 3.2 Track (inside `fusion[]`) — `Track.to_dict` in `dexia/fusion/engine.py`
```jsonc
{
  "track_id": "TRK-ARM-002",        // TRK-<CAT>-<n>
  "category": "armor",             // armor|apc|infantry|artillery|air_defense|ew|emitter|air
  "position": [3120.5, 290.1],      // [x, y] METRES, sim frame. NOTE: 2D (fused from detections)
  "velocity": [-4.0, 0.1],          // [vx, vy] m/s (estimated)
  "uncertainty_r": 12.3,            // 1-sigma position radius (m) — draw as a circle
  "confidence": 0.82,               // 0..1 — drives color/opacity
  "sources": ["uav_eo","ugs"],     // which feeds built this belief (provenance)
  "status": "active",              // active | coasting | stale  (coasting = unseen, decaying)
  "first_tick": 3, "last_seen_tick": 12
}
```
Confidence model intuition for the legend: SIGINT alone ≈0.4 (vague), +EO ≈0.82 (confirmed).

### 3.3 Decision (inside `decisions[]`) — `Decision.to_dict` in `dexia/agent/policy.py`
```jsonc
{
  "track_id":"TRK-ARM-002", "asset":"m777_howitzer", "cmd":"request_fires",
  "kind":"fires",                  // collect | suppress | fires | strike  ← color the timeline by this
  "target":[3120,290], "why":"...rationale (Korean)...", "score":0.71
}
```
The timeline's spine = the ordered `kind`s across cycles. Canonical kill-chain order:
`collect` (ISR) → `suppress` (jam) → `fires`/`strike`.

### 3.4 Option (inside `asset_match[track_id][]`) — `Option.to_dict` in `dexia/agent/assetmatch.py`
```jsonc
{ "asset":"m777_howitzer", "cmd":"request_fires", "feasible":true, "score":0.71, "reason":"in range, ammo 9" }
```
Use this for a "why this asset (and not that one)" drill-down panel — it shows the AI *derived*
feasibility (range/ammo) rather than inventing it.

### 3.5 EffectEvent (inside `events[]`) — `EffectEvent.to_dict` in `dexia/fusion/effects.py`
```jsonc
{
  "tick":18, "action":"fire",      // fire | strike | jam | isr
  "status":"impact",               // launched | impact | suppressed | tasked | rejected | no_effect
  "asset_id":"m777_howitzer", "detail":"1 neutralized @ [4500,0]",
  "killed":["bmp_apc"],
  "trajectory":[[x,y,z], ...]       // P4 BALLISTIC ARC (only populated on terrain scenarios; [] on flat)
}
```
`trajectory` is your `TrajectoryLayer` data: plot the `[x,y]` polyline on the map; `z` is the arc
height (use for an elevation sparkline, or vary stroke opacity with altitude).

### 3.6 `scenario_evals.jsonl` — one per line = one scenario result (`MissionResult` asdict, `campaign.py`)
```jsonc
{
  "scenario":"korea-003","theater":"korea","intent":"deny","outcome":"success_destroyed",
  "score":0.95,"neutralised":6,"red_total":6,"blue_lost":0,"cycles":7,
  "decisions_by_kind":{"collect":2,"suppress":1,"fires":4},"governance_accept_rate":0.91
}
```
`outcome` ∈ `success_destroyed | fail_breach | fail_blue_loss | in_progress`. Aggregate by `theater`
for the scoreboard (pass = score ≥ 0.7).

### 3.7 Scenario + terrain (for ScenarioPicker + map framing) — `dexia/scenario/scenario.py`
A scenario YAML: `id`, `theater`, `mission.{intent,tasking,victory}`, `blue[]`/`red[]` force elements
(`cls`, `n`, `pos:[x,y]` or `route:[[x,y],...]`, `behavior`, optional `hero`), `feeds[]`, and optional
`terrain: {type:hill|ramp|flat, peak, center:[x,y], sigma, size, dx, origin}`. Positions are **metres in
the sim frame**, spanning roughly ±8000 m for wargame scenarios.

---

## 4. Frontend conventions (match these — don't reinvent)

The existing `dexia-hud/` is the **legacy drone GCS** (telemetry, drone garage, HITL). Study these
files; your wargame HUD is a **new section/route** that reuses their patterns but its own data:

- **Map** — `components/TacticalMap.js`: MapLibre GL, created once, overlays updated **imperatively**
  (`source.setData(...)`, `setPaintProperty`, `Marker.setLngLat`) so high-rate updates never re-render
  the WebGL canvas. Basemaps: ESRI satellite / CARTO dark / OpenTopo; AWS Terrarium DEM available
  (`ENABLE_TERRAIN`, currently off). **Reuse this imperative pattern** for TrackLayer/LOS/Trajectory.
- **Geo** — `lib/geo.js`: `localToLngLat([x,y], anchor, scale)` projects sim-metres → lon/lat;
  helpers `lineGeoJSON`, `threatCircleGeoJSON`, `directionalConeGeoJSON`, `rectGeoJSON`.
  ⚠️ **CRITICAL GAP:** geo.js is tuned for the **~15 m drone arena** (`WORLD_SCALE = 8`, `ARENA_HALF_M
  = 120`, zoom 18.5). The **wargame is km-scale** (positions ±8000 m). For the wargame map you MUST
  use **`scale = 1`** and a far lower zoom (~12–13), and a theater anchor. Add a `WARGAME_ANCHOR` +
  pass `scale=1` to the geo helpers (they already take `scale` as a param) — do not repurpose the
  drone `WORLD_SCALE`. The terrain ridge lives in sim metres; align the map to it.
- **API routes** — `pages/api/*.js`. Two patterns exist:
  1. **Proxy to FastAPI** (`pages/api/evals.js` → `http://127.0.0.1:8000`). Used by the live drone sim.
  2. **Self-contained** Next route (read a file / compute). **Use pattern (2) for the wargame**, because
     the wargame is an *offline batch* producer (no running server) — see §5.

---

## 5. Data wiring — the one architectural decision, and the recommendation

The wargame core is **offline/batch**: `MissionRunner.run()` writes `reasoning_trace.jsonl`; the
campaign writes `scenario_evals.jsonl`. There is **no live wargame server** (the FastAPI `sim_api.py`
on :8000 serves the *drone* sim + ontology + drone-evals, NOT the wargame).

**Recommended (simplest, no new server dependency):** Next.js API routes that read repo files with
`fs`, plus one route that runs a scenario by spawning Python.

| Route | Method | Does |
|---|---|---|
| `pages/api/scenarios.js` | GET | list scenario ids/theaters by scanning `scenarios/**/*.yaml` (parse front matter via a tiny yaml read, or shell `python -c`) |
| `pages/api/trace.js` | GET | read repo-root `reasoning_trace.jsonl`, return parsed array of cycle records |
| `pages/api/campaign.js` | GET | read repo-root `scenario_evals.jsonl`, return parsed results |
| `pages/api/run.js` | POST `{scenario}` | spawn `python -m ...` to run that mission, regenerate `reasoning_trace.jsonl`, then return it |

Resolve the repo root from the Next process: `path.join(process.cwd(), '..')` (the HUD runs in
`dexia-hud/`, files are one level up) — verify and centralize this in a `lib/wargamePaths.js`.

Spawning Python: `child_process.execFile(pythonExe, ['-m','dexia.agent.loop_cli','--scenario',id])`.
You will likely add a tiny **`dexia/agent/loop_cli.py`** (`argparse --scenario`, calls MissionRunner)
— that's the *only* Python addition allowed for #8, and it just wraps existing code (no core change).

*(Alternative if you prefer a server: add wargame routes to `dexia/api/sim_api.py` and proxy like
`evals.js`. More moving parts; only do this if you want live streaming.)*

---

## 6. Component-by-component plan

All components are **presentational over the JSON in §3**. Suggested new route: a page
`pages/wargame.js` hosting a left rail (ScenarioPicker + CampaignScoreboard), a center map
(TrackLayer + LosOverlay + TrajectoryLayer on one MapLibre instance), and a bottom/right
ReasoningTimeline, with a shared `cycleIndex` play/scrub state.

1. **`ScenarioPicker.js`** — GET `/api/scenarios`; list grouped by theater; selecting one POSTs
   `/api/run` then loads the trace. Show intent + force counts + a "terrain" badge.
2. **`ReasoningTimeline.js`** — GET `/api/trace`; render cycles as a vertical timeline. Each cycle:
   the `decisions[]` as chips colored by `kind` (collect=blue, suppress=amber, fires=red, strike=violet),
   the `reasoning` string, and `gaps[]` ("why it couldn't act yet"). Clicking a cycle sets the global
   `cycleIndex` (drives the map). A play button steps cycles on a timer. This is the centerpiece —
   make the collect→suppress→fires story legible.
3. **`TrackLayer.js`** — for the selected cycle, draw `fusion[]`: a marker per track at
   `localToLngLat(position, WARGAME_ANCHOR, 1)`, a `threatCircleGeoJSON(position, uncertainty_r, …, 1)`
   ring, color/opacity by `confidence`, a `status` style (coasting = dashed/faded). Tooltip: category,
   confidence, `sources` (provenance), velocity. Use imperative `setData` keyed off `cycleIndex`.
4. **`LosOverlay.js`** — draw sensor→track sightlines. You need sensor (blue platform) positions; the
   trace doesn't carry blue truth, so either (a) expose them via `/api/run` output (preferred: have
   `loop_cli` also dump `world_snapshot.json` with blue/red 3D positions + the terrain id), or
   (b) recompute from the scenario. Color a line red if occluded. To know occlusion, reuse the Python
   `Heightfield.raycast` / `clear_los` (in `dexia/physics3d`) — expose a precomputed `los: bool` per
   (sensor,track) pair from the run, rather than reimplementing raycast in JS.
5. **`TrajectoryLayer.js`** — for cycles whose `events[].trajectory` is non-empty, draw the `[x,y]`
   polyline (a `LineString`) with a launch/impact marker; optionally an inset elevation profile from `z`.
6. **`CampaignScoreboard.js`** — GET `/api/campaign`; KPI cards (pass rate, mean score, mean
   neutralised) + a by-theater table + an outcomes breakdown. Mirror the numbers
   `python -m dexia.agent.campaign` prints.

---

## 7. Regression discipline & boundaries (non-negotiable)

- **The HUD is read-only over the sim's outputs.** Do not modify `dexia/fusion`, `dexia/agent/{loop,
  policy,assetmatch,campaign}`, `dexia/scenario`, `dexia/physics3d`, or the catalog. The single
  allowed Python addition is a thin CLI wrapper (`loop_cli.py`) that calls existing `MissionRunner`.
- **Don't break the 76-test suite or campaign parity.** After any work:
  `python -m pytest tests/ -q --ignore=tests/test_phase86_doctrine_enforcement.py` (76 pass) and
  `python -m dexia.agent.campaign --count 20` must still print pass 95% / score 0.9 / neutralised 86%
  / `{in_progress:16, success_destroyed:4}`.
- **Coordinates:** sim frame is metres, ENU (`x`=East, `y`=North, `z`=Up). Tracks/decisions are 2D
  `[x,y]`; entity truth + trajectories are 3D `[x,y,z]`. Use `scale=1` for the wargame map.
- **Both worlds:** flat scenarios (no `terrain`, `trajectory=[]`, no occlusion) AND 3D terrain
  scenarios must both render. Degrade gracefully (no terrain → skip LOS/arc layers).
- Match existing code style: plain JS, functional React components, imperative MapLibre overlays,
  Korean is fine in UI copy (the rationale strings are Korean).

---

## 8. Definition of done

1. `pages/wargame.js` loads in `npm run dev`, lists scenarios, and replays a selected mission.
2. ReasoningTimeline shows the per-cycle collect→suppress→fires decisions with rationale; scrubbing a
   cycle updates the map.
3. TrackLayer renders fused tracks with confidence + provenance; coasting/stale tracks are visually
   distinct.
4. On `ridge-los-p4`: the behind-ridge target shows EO-occluded (LosOverlay red) while radar sees it,
   and the howitzer's ballistic arc is drawn (TrajectoryLayer).
5. CampaignScoreboard reproduces the campaign aggregates.
6. Python suite still 76-green; campaign parity unchanged.
7. A short `dexia-hud/README` section: how to generate data + run the HUD.

---

## 9. Glossary
**AIP** — the wargame product (Catalog→Scenario→Fusion→AssetMatch→Policy→ActionBus→Effects).
**Track** — a fused belief about one enemy unit (position, confidence, sources).
**Feed** — a sensor model (`uav_eo` EO/IR, `ugs` acoustic, `sigint` ELINT, `gsr` ground radar).
**collect/suppress/fires/strike** — the four decision kinds (ISR / jam / artillery / loiter munition).
**hero aircraft** — one marked aircraft flown on a JSBSim 6-DOF FDM (P2); others use a numpy model.
**terrain-gated** — 3D physics/LOS/ballistics activate only when a scenario declares `terrain:`.
