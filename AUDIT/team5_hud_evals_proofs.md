# Team 5 Audit — Frontend↔Backend Contract, Evals Integrity, RL Envs, Proofs

Scope: `dexia-hud/` source, `dexia/evals/`, `dexia/scenario/`, `dexia/envs/`, root `verify_*.py` / `eval_*.py` / `train_*.py`, and result artifacts (HTML/PNG/JSONL).
Date: 2026-06-21. Method: traced full producer→consumer paths; verified by reading code + sampling artifacts, not by names.

---

## VERDICT SUMMARY

- **Dashboards / verify scripts: MOSTLY REAL DATA, not canned.** `phase1_results.html`/`phase3_results.html` are real Plotly plots of computed float trajectories (base64 arrays), `evals_results.jsonl` is genuinely produced by the live `EpisodeEvalSuite` (1554 real append-only records incl. honest `null`/`n/a` metrics), `scenario_evals.jsonl` is produced by the actual `MissionRunner` loop. They ARE last-run snapshots (replay), not live — but they reflect real computation. The HTML/PNG are static snapshots; they are not regenerated on view, so they can silently go stale.
- **The single worst defect is a SCORING-INTEGRITY one, not a fake-data one:** the wargame kill chain is *one-directional* — nothing in the engine can ever kill a Blue unit — so `blue_lost` is structurally always 0, which hands every campaign mission a free 0.3 of its score and makes the entire "kill vs loss balance" shown to the operator decorative. See CRITICAL-1.
- **Worst frontend↔backend contract mismatch:** `verify_workflow.py` connects to the command server on the WRONG PORT (`ws://localhost:8000` vs the server's `8001`), so the project's own end-to-end workflow "proof" cannot connect to the thing it claims to prove. See CRITICAL-2 / HIGH-3.

---

## CRITICAL

### CRITICAL-1 — Red force can never harm Blue; `blue_lost` is structurally always 0, inflating every campaign score
**Files:** `dexia/fusion/effects.py:96-103,132-141`; `dexia/agent/red_commander.py:17-59`; `dexia/agent/loop.py:172-183,210`; `dexia/agent/campaign.py:31-47`; `dexia/agent/mission_manager.py:117,124`

`EffectResolver` only ever sets `e.alive = False` on `self.world.red` (effects.py:99-100, 137-138). There is **no code path anywhere** that kills a Blue unit. `RedCommander.step()` (red_commander.py) only *moves* red units (retreat/flank/emitter toggling) — red SAMs never fire. The air-defense engagement primitives `sam_can_engage`/`engage_air` exist (effects.py:243-265) but are explicitly documented as **"Kept as module functions (not wired into MissionRunner)"** (effects.py:240-242). Consequently:

- `MissionRunner._status()` computes `blue_lost = self._blue0 - len(self.world.blue)` (loop.py:173); this is **always 0**, so the `fail_blue_loss` branch (loop.py:175) is unreachable in campaigns.
- `score_mission()` adds `0.3 * (1.0 if blue_ok else 0.0)` (campaign.py:39) where `blue_ok` is true whenever `blue_lost <= max_loss` — i.e. **always true → a guaranteed free 0.3** on every scenario.
- The live operations backend (`MissionManager`) uses the *same* resolver + RedCommander (mission_manager.py:76,117,124), so the operations HUD shows an enemy that cannot damage friendly forces. The `est_loss` ("아군손실") number in `AipStaffPanel` is never validated against any real attrition.

**Evidence:** all 20 rows of `scenario_evals.jsonl` show `"blue_lost": 0`, and `"governance_accept_rate": 1.0` on every single run.
**Blast radius:** the headline "mission accomplished / own force preserved" metric, the campaign pass-rate eval, and the operator's kill-vs-loss decision UI are all built on a quantity that can only ever be zero.

### CRITICAL-2 — `scenario_evals.jsonl` scores time-outs (`in_progress`) as successes
**Files:** `dexia/agent/campaign.py:31-47`; `dexia/agent/loop.py:106-160,172-183`

`MissionRunner.run()` caps at `max_cycles=16` and sets `outcome="in_progress"` if Red ground is not fully destroyed (loop.py:108,157-160,183). But `score_mission()` (campaign.py:36-39) scores `0.7 * neutralised_fraction + 0.3 * blue_ok` regardless of whether the mission ever concluded. A run that **timed out without finishing** still scores by partial neutralisation + the free 0.3 (see CRITICAL-1).

**Evidence:** in `scenario_evals.jsonl`, 16 of 20 rows are `"outcome": "in_progress"` yet score **0.689–0.953**, and most clear `PASS_SCORE=0.7` (campaign.py:28). The reported campaign "pass rate" therefore counts unfinished missions as passes.
**Blast radius:** the campaign success metric (the "quantitative proof the system does its role", per the module docstring) is materially inflated; an in-progress timeout is presented as mission success.

---

## HIGH

### HIGH-3 — `verify_workflow.py` targets the wrong server/port — the E2E "proof" cannot connect
**Files:** `verify_workflow.py:10`; `dexia/agent/command_server.py:80,172-173`; `dexia-hud/pages/operations.js:18`

`verify_workflow.py` uses `uri = "ws://localhost:8000/ws/command"`. The WebSocket command server (`command_server.py`) binds **port 8001** (`uvicorn.run(app, host="0.0.0.0", port=8001)`), and the actual HUD (`operations.js:18`) connects to `ws://localhost:8001/ws/command`. Port 8000 is the *evals/control* FastAPI (`sim_api.py`), which has **no** `/ws/command` route (it exposes only `/api/sim/*`, `/api/evals/*`, `/ontology/*`). So the project's own end-to-end workflow verifier connects to the wrong process and can never exercise the kill chain it claims to prove.
**Blast radius:** the "honest verification" E2E proof for the operator workflow is non-functional; it would hang to its 180s timeout or fail to connect, masking real regressions.

### HIGH-4 — Two HUDs / two backends / two coordinate frames; geo anchors don't cover the battlefield regions
**Files:** `dexia-hud/lib/geo.js:13-43,103`; `dexia-hud/components/operations/LiveMap.js:5,175-177`; `dexia/scenario/battlefields.py:22-53`; `dexia/agent/battle_generator.py:51-71`; `dexia-hud/pages/wargame.js:51,87`

Two unrelated frontend↔backend stacks coexist with incompatible spatial models:
- **GCS** (`index.js`+`geo.js`+`telemetry_stream.py`): ~15 m MuJoCo arena, `WORLD_SCALE=8`, `ARENA_HALF_M=120`, anchored Seoul; target at `[5,5,1]`.
- **Operations** (`operations.js`+`LiveMap.js`+`MissionManager`): ±7000 m frame, `M2DEG=1/111000` (scale=1), anchored from `gameState.battlefield.location`.

Independently this is acceptable, but the anchor tables are inconsistent with the data: `geo.js` `THEATER_ANCHORS` only defines `eastern_europe/ukraine/ua_east/korea/default`, whereas `battlefields.py` emits regions `middle_east/korea/russia/europe/southeast_asia` and scenario theaters are `procedural_hills`/`desert_storm`. `wargame.js:87` does `setAnchor(theaterAnchor(theater))`, which falls back to the Donbas `default` for every theater except Korea — so 4/5 battlefield regions and the desert/procedural scenarios render at the wrong geographic location. (The operations `LiveMap` is insulated because it reads `battlefield.location` directly, which is correct.)
**Blast radius:** wargame-replay map places forces in the wrong country; any future code that reuses `theaterAnchor` inherits the silent default.

### HIGH-5 — "Live mission eval" folds GLOBAL, cross-run audit history into a single-tick episode score
**Files:** `dexia/evals/suite.py:38-66,89-120`; `dexia/evals/audit.py:49-122`; `dexia-hud/pages/api/evals.js`; `dexia/api/sim_api.py:429`

`episode_from_telemetry()` builds an `EpisodeRecord` from **one** telemetry snapshot, but `EpisodeEvalSuite.evaluate()` appends `llm_accuracy` and the `observability` block from `observability_summary()`, which reads the **entire** `llm_audit.jsonl` / `action_audit.jsonl` files (audit.py:49-88 read all rows, no episode/time filter). So the LLM-accuracy metric and audit counters in a "current mission" eval reflect **every LLM/action call ever logged across all past runs**, not this mission. The `llm_audit.jsonl`/`action_audit.jsonl` are 85 KB/482 KB of accumulated history.
**Blast radius:** the HUD "평가 실행" scorecard reports a global-historical LLM ok-rate and accept-rate as if they were the live mission's, mislabeling cross-run aggregates as per-episode results. Combined with the broadcast-latency-as-current-tick collapse (suite.py:91-94,115), a single-tick eval is not a meaningful episode eval.

### HIGH-6 — HUD hardcodes attrition denominator `/6`, decoupled from the actual force size
**Files:** `dexia-hud/pages/index.js:391`; `telemetry_stream.py:351-367` (`make_interactive_env`: `pool_size=12`, `num_recon=0, num_kami=0`)

`RightPanel` renders `ATTRITION ${lost} / 6` and `pct = (lost/6)*100` with a literal 6. The streamer the GCS actually talks to starts with **0** pre-placed drones and a **pool of 12** deployable units; total live force is operator-driven, never fixed at 6. The denominator is a leftover from the old `num_recon:2 + num_kami:4` scripted scenario.
**Blast radius:** the attrition gauge is wrong for the interactive scenario the HUD runs — it can show <100% when all live drones are lost, or a misleading fraction as the operator scales the swarm.

---

## MEDIUM

### MEDIUM-7 — AIP can only `fires`/`strike` against ground armor; SAM/EW targets are unkillable-by-design and silently uncounted
**Files:** `dexia/agent/assetmatch.py:15-16,50,60,69`; `dexia/agent/loop.py:169-183`; `dexia/agent/campaign.py:36`

`GROUND = {"armor","apc","infantry","artillery"}`; `match_assets` only offers `fires`/`strike` when `track.category in GROUND` (assetmatch.py:50,60). `air_defense`/`ew`/`emitter` can only be `jam`med (assetmatch.py:69). Scoring's `red_ground_total`/`red_ground_remaining` also filter to GROUND (loop.py:170, campaign.py:36). So SAMs and EW are never strike targets and never enter the win condition — the "destroy the air-defense" objective is structurally impossible, and a scenario heavy in AD can never reach `success_destroyed`. This is the scoring-side analogue of the known "air target unreachable" class of bug: AD threats are excluded from the kill objective rather than being made reachable.

### MEDIUM-8 — Ground-fire aimpoint is forced to terrain height; an airborne target would be unkillable
**Files:** `dexia/fusion/effects.py:82-103,122-141`

`_do_fire`/`_do_strike` set the aimpoint z to `terrain.height(x,y)` (ground) and then test `distance3d(target_unit.position, aim) <= lethal_r`. For any target with real altitude (domain `air`), the 3D distance from a ground aimpoint to an airborne unit exceeds `lethal_r`, so it cannot be killed. Currently masked only because `battle_generator` red pools are all ground/AD (battle_generator.py:43-48) — but the moment an air red is introduced (catalog supports `domain: air`, equipment_catalog.yaml), strikes silently miss. This is the live-engine form of the "air altitude blocks winnable game" backlog item.

### MEDIUM-9 — HTML/PNG "proofs" are un-versioned last-run snapshots presented as current
**Files:** `phase1_results.html`, `phase3_results.html`, `dashboard.png`, `operations.png`, `dashboard_active.png`, `operations_active.png`; producers `test_phase1.py`, `eval_phase3.py`

The Plotly HTML *is* real computed data (verified: base64 float trajectory arrays, not templated), and the PNGs are screenshots — but none are regenerated on view and none carry the run's git SHA / timestamp / input hash. `dashboard.png` and `operations.png`/`operations_active.png` are dated 2026-06-05 while the engine has changed since (telemetry/env files dated 2026-06-21). A reviewer opening these sees a 16-day-old render with no staleness signal. `operations.png` and `operations_active.png` are byte-identical in size (227954) — likely the same image duplicated as "before/after".

### MEDIUM-10 — `useTelemetry` polling-fallback cleanup is fragile (timer sentinel object)
**Files:** `dexia-hud/lib/useTelemetry.js:74-118`

The SSE→poll fallback reassigns `pollTimer` to a sentinel object carrying two interval ids and relies on `Symbol.toPrimitive`. If `startPolling()` is re-entered via the `fatal`/`onerror` SSE handlers (lines 66-67) while a sentinel is already installed, the earlier `_sse` retry interval is overwritten and **leaked** (never cleared), since the effect cleanup only clears the *current* `pollTimer` object. Under repeated Redis flaps this leaks 5s reconnect timers. Functional, but a real resource leak on the live telemetry path.

---

## Paths that are SOUND (verified, not just named)

- **GCS command path** `index.js → /api/command → sim_api /api/sim/* → command_queue → telemetry_stream.drain_commands`: action mapping (command.js:24-46) matches the FastAPI request models and the streamer's `apply_command`; API key attached server-side; governed funnel real. Consistent loop.
- **Operations WS contract** `operations.js ↔ command_server.py ↔ MissionManager.get_client_state`: message types (`STATE_UPDATE/BATTLEFIELDS/INFO/RESET_ACK`) and field shapes (`blue_details[].id/cls/pos/sensor/weapon`, `tracks[].track_id/category/position/uncertainty_r/confidence`, COA `id/action/target/p_kill/est_loss/rank/description`, `feed[].type/message`, `enemy_area`, `battlefield`) all match producer→consumer. (Spatial/attrition issues are the separate findings above.)
- **`dexia/evals/metrics.py` + `suite.py`**: kill_efficiency, recon_survival, net_survivability, aa_engagement, broadcast_latency are genuinely derived from real episode quantities with correct `_safe_div` denominators and honest `null`/`n/a` exclusion from the verdict. (The cross-run audit fold is HIGH-5.)
- **RL env reward** (`drone_marl_env.py:593-696`): `kill_event`, `newly_lost` (AA + crash), `network_surv`, `detection_event` are connected to true sim state; done = `kill_confirmed | all_recon_lost | all_kami_lost`; obs target-slot is correctly zero-masked until broadcast (lines 296-304). Reward measures the intended outcome.
- **`verify_battlefields.py`**: an honest proof — actually generates all 100 battlefields, asserts fog/terrain/enemy-area invariants and region terrain divergence on live `WorldState`s.
- **`eval_phase9.py`**: a real checkpoint rollout reduced to an `EpisodeRecord`, not a canned report.

---

## PRIORITIZED ONE-LINERS (CRITICAL/HIGH)

1. CRITICAL — `dexia/fusion/effects.py:96-141` + `red_commander.py` + `loop.py:173`: no code path kills Blue; `blue_lost` always 0 → free 0.3 on every `score_mission` and unreachable `fail_blue_loss`.
2. CRITICAL — `dexia/agent/campaign.py:31-47` + `loop.py:157-160`: `in_progress` (16-cycle timeout) missions are scored as partial success; 16/20 `scenario_evals.jsonl` rows are in_progress yet score 0.69–0.95, most "PASS".
3. HIGH — `verify_workflow.py:10`: connects to `ws://localhost:8000` but command server is on `8001` → the E2E workflow proof cannot connect (port 8000 is the evals API with no `/ws/command`).
4. HIGH — `dexia-hud/lib/geo.js:28-43` + `wargame.js:87`: `THEATER_ANCHORS` lacks middle_east/russia/europe/southeast_asia/desert_storm/procedural_hills → 4/5 regions render at the Donbas default location.
5. HIGH — `dexia/evals/suite.py:41-45` + `audit.py:49-88`: single-tick live eval folds GLOBAL cross-run llm_audit/action_audit history into the "current mission" scorecard.
6. HIGH — `dexia-hud/pages/index.js:391`: attrition gauge hardcodes `/6`, but the live interactive env has 0 pre-placed drones + pool of 12 → wrong denominator.
