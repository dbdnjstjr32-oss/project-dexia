# Team 4 Audit — Server / Runtime / Auth / Deploy / Persistence

Scope: `dexia/api/sim_api.py`, `dexia/api/auth.py`, `dexia/api/llm_gateway.py`, `dexia/agent/command_server.py`, `dexia/runtime/*`, `dexia/sitl_bridge.py`, `telemetry_stream.py`, `run_gcs_simulation.py`, `dexia.config.yaml`, `docker-compose.yml`, `docker/*`, `requirements*.txt`, plus the cross-cutting IPC path (`dexia/integrations/command_queue.py`, `dexia/ontology/store.py`, `dexia-hud/lib/commandStore.js`, `dexia-hud/pages/api/command.js`).

Date: 2026-06-21. Verdict: the live loop is functional but the auth model is effectively decorative, and the "single governed write-funnel" is bypassable by design. Several CRITICAL items below.

---

## CRITICAL

### C1 — The only API credential is a hardcoded, publicly-known default key; the prod config ships the same keys
- **Files:** `dexia/api/auth.py:30-33`, `dexia.config.yaml:56-59`, `dexia-hud/pages/api/command.js:21`
- **What's wrong:** `DEFAULT_PRINCIPALS` hardcodes `dexia-commander` (commander clearance) and `dexia-operator` in source. The shipped `dexia.config.yaml` defines the *same two literal keys* with the same clearances, so even when config "overrides" the default the credentials are identical and committed to the repo. The Node command route hardcodes `SIM_API_KEY = process.env.SIM_API_KEY || 'dexia-commander'` — the commander key is literally in the frontend repo. The auth docstring claims keys are "local config, override for real use," but nothing forces an override and the default *is* the config.
- **Blast radius:** Anyone who can reach port 8000 can send `X-Dexia-Key: dexia-commander` and drive every mutating endpoint at commander clearance — `deploy`, `activate` (ARM, "AA goes live", physics on), `clear`, `recall`. The entire clearance lattice (`operator < commander`, C3) is meaningless because the highest-privilege key is a known constant. For a "military drone simulation" control plane this is a full authz bypass with one guessable header.

### C2 — `commands.json` is a multi-writer IPC channel with no shared lock across the Node ↔ Python boundary, and the Node fallback writes are ungoverned
- **Files:** `dexia-hud/lib/commandStore.js:21-39`, `dexia-hud/pages/api/command.js:71-90`, `dexia/integrations/command_queue.py:217-242`, `telemetry_stream.py:445-449`, `dexia/envs/drone_marl_env.py:856-881`
- **What's wrong:** Three independent processes touch `commands.json`: (a) FastAPI `append_command` (holds the `commands.json.lock` advisory lock), (b) the streamer `drain_commands` (holds the same lock), and (c) the Next.js route's `enqueueCommand` → `writeQueue` (`writeFileSync(tmp)` + `renameSync`, **no lock at all**, sidecar lock file never opened). The command_queue docstring explicitly admits "the Node `/api/command` route does not share our lock." On Windows `renameSync` over a file a Python reader has momentarily open throws `EPERM`/`EBUSY` (uncaught in `writeQueue`) → an unhandled 500 in the HUD; in the other direction the Node writer can clobber a command the Python side just appended (lost-update: read-modify-write with no mutual exclusion).
- **Governance bypass:** The fallback branch (`command.js:71-90`) fires whenever the FastAPI control plane is unreachable and writes raw `{action:'spawn'|'remove'|'clear'}` straight into `commands.json`. The streamer's `drain_commands()` → `env.apply_command()` (`drone_marl_env.py:856`) executes those verbatim — **no clearance check, no ActionBus ontology guard, no lineage row**. The system's central claim ("THE funnel: nothing writes state by any other path," `sim_api.py:145-149`) is false: kill the API container (or just let one request time out) and the HUD silently deploys/arms drones with zero governance. Any local process that can append to `commands.json` has the same ungoverned write.
- **Blast radius:** Lost/duplicated C2 commands under burst, intermittent HUD 500s, and a complete governance bypass of the audited write-funnel — the audit/lineage trail that justifies the whole AIP design becomes incomplete and untrustworthy.

### C3 — `dexia/agent/command_server.py`: no authentication, CORS fully open, single global mutable session shared across all clients
- **File:** `dexia/agent/command_server.py:18-26, 46-53, 80-169`
- **What's wrong:** This FastAPI app (the Operations UI backend, port 8001) has `CORSMiddleware(allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])` and a single unauthenticated WebSocket `/ws/command`. There is **no auth dependency anywhere** — every command (`START`, `APPROVE`, `REJECT`, `STRIKE_OPTIONS`, `MODIFY`, `RESET`, `SET_SPEED`) is accepted from any origin/any connection. `allow_origins=["*"]` together with `allow_credentials=True` is itself invalid per the CORS spec (browsers reject the combination) and signals copy-paste security.
- **All session state is one process-global dict** (`session = {"manager", "running", "task"}`, line 49). Every connected client mutates the *same* `MissionManager`: a second browser tab issuing `START` overwrites the first client's mission and `cancel()`s its task; `RESET` from anyone nukes everyone. There is no per-connection isolation and no lock around `session` even though `_stepping_loop`, `_broadcast_loop`, and the WS handler all read/write it concurrently.
- **Blast radius:** Any page on the internet can open a WS to this port and start/stop/modify missions; concurrent operators corrupt each other's state. Combined with C1, the control surface is essentially open.

---

## HIGH

### H1 — `command_server` leaks/abandons asyncio tasks and never disconnects on broadcast failure
- **File:** `dexia/agent/command_server.py:39-44, 122-125, 157-163`
- **What's wrong:** On `START`, `session["task"].cancel()` is called but never `await`ed, so the old `simulation_loop` (a `gather` of two infinite loops) is cancelled without confirmation — restarts can race two loops briefly. `manager.broadcast` swallows every send exception (`except Exception: pass`) but never removes the dead WebSocket from `active_connections`, so disconnected clients accumulate forever and every tick re-attempts a send to each corpse. `ConnectionManager.disconnect` is only called on `WebSocketDisconnect`; a send-side failure leaves the socket in the list permanently (unbounded growth, wasted broadcast work each tick).
- **Blast radius:** Slow memory/handle leak and degrading broadcast latency the longer the server runs — directly hurts live-loop reliability.

### H2 — `command_server` runs blocking work on the event loop; partial `to_thread` coverage
- **File:** `dexia/agent/command_server.py:55-78, 129-166`
- **What's wrong:** `run_cycle`, `command_recon`, and `modify_plan` are offloaded via `asyncio.to_thread` (good), but `APPROVE`/`REJECT`/`STRIKE_OPTIONS`/`BDA` call `session["manager"].generate_coa_options()` / `assess_bda()` / `approve_coa()` **synchronously inside the async WS handler**. `mission_manager.py` is 42 KB and these paths can invoke the LLM gateway (25 s timeout, `llm_gateway.py:19`). A `STRIKE_OPTIONS` while the model is cold blocks the entire event loop — the `_broadcast_loop` stops streaming to *all* clients until it returns. The 2 Hz broadcast guarantee in the docstring is not actually upheld for these branches.
- **Blast radius:** HUD freezes (no telemetry/state updates) for up to ~25 s whenever an operator requests options/BDA — the live loop visibly wedges.

### H3 — Process-wide config is cached once and never reloaded; env overrides only apply per-process
- **File:** `dexia/runtime/config.py:148-157`, used at `sim_api.py:77, 451-456`
- **What's wrong:** `get_config()` memoizes `_CONFIG` for the life of the process and `load_config` reads env *at first call*. `_monitor()` (`sim_api.py:71-78`) captures `stall_seconds` once at first construction. There is no way to change scenario/Hz/thresholds without a full restart, and any code that sets env vars after import is ignored. `cfg.apply()` mutates the global `evals.metrics.THRESHOLDS` dict as a side effect of reading config — a hidden global write that makes eval behavior depend on import/call order across the API, evals_worker, and streamer.
- **Blast radius:** Config drift between the 4 services (each has its own cached copy); thresholds can differ between `dexia-api` and `dexia-evals` depending on who called `.apply()` and when.

### H4 — Deployment drift: pinned/unpinned dep mismatch and Dockerfiles that can't run the code they ship
- **Files:** `docker/Dockerfile.evals:8-15`, `docker/Dockerfile.api:8-11`, `docker-compose.yml:58-67`, `requirements*.txt`
- **What's wrong:**
  - `Dockerfile.evals` `COPY requirements-runtime.txt .` then installs **only** `PyYAML` (ignores the copied file). `evals_worker` imports `dexia.evals` and `dexia.runtime` which import `numpy` (`requirements-runtime.txt` lists `numpy>=1.26`; the OAG Logic blocks need it). The evals image will `ImportError` at runtime on numpy. It also runs `dexia.runtime.evals_worker`, but `evals_worker` → `dexia.evals` may pull `fastapi`/`pydantic` transitively that aren't installed.
  - All deps are unpinned floors (`fastapi>=0.110`, `ray[rllib]>=2.9`, `mujoco>=3.0`, `torch` from the CPU index with no version). Two images built a week apart get different transitive trees — non-reproducible, and FastAPI/pydantic v2 churn breaks easily.
  - `Dockerfile.api` (python:3.13) installs `requirements-runtime.txt` which includes `ollama>=0.2`; fine, but the API also imports `dexia.evals`/`dexia.ontology` — confirm those don't transitively need MuJoCo/Ray (they shouldn't, but it's load-bearing and unpinned).
  - `dexia-evals` has `depends_on: [dexia-sim]` but no healthcheck condition, and it reads `telemetry.json` from the bind mount; on a cold stack it spins logging "no telemetry yet" forever, which is fine, but `restart: unless-stopped` + the numpy ImportError = crash-loop.
- **Blast radius:** `docker compose up` does not reliably bring up a working evals service; non-pinned deps make every rebuild a roll of the dice.

### H5 — Windows-hardcoded paths and host assumptions leak into the "portable" stack
- **Files:** `run_gcs_simulation.py:16`, `telemetry_stream.py:14, 21-23` (docstrings), `dexia.config.yaml` (llm host comments), `docker-compose.yml:51`
- **What's wrong:** `run_gcs_simulation.py:16` hardcodes `C:/Users/dbdnj/.gemini/antigravity/brain/<uuid>` as an artifacts dir — breaks for any other user/host and in-container. The streamer/HUD assume `..` relative cwd for `telemetry.json`/`commands.json` (`telemetry.js:14`, `commandStore.js:9`), which only holds because compose bind-mounts the repo at a fixed path; run the HUD container from a different `working_dir` and the relative `..` escapes the mount. `OLLAMA_HOST=http://host.docker.internal:11434` assumes Docker Desktop / the `host-gateway` extra_host on Linux — works, but the air-gap story (config `airgap: true`) and host-resident model on `8 GB VRAM` is single-host-only and undocumented as a hard requirement.
- **Blast radius:** "Works on my machine" — the deploy is effectively pinned to the author's Windows laptop + local Ollama.

### H6 — SITL `MockUDPLink` socket can leak; bridge has no read timeouts and lazy-connects without cleanup
- **File:** `dexia/sitl_bridge.py:128-153, 203-216`
- **What's wrong:** `send_action` lazily calls `self.link.connect()` if not connected but nothing in `SITLBridge` ever calls `close()`. In non-`dry_run` mode `MockUDPLink.connect` opens a real UDP socket; a long-lived bridge that's recreated (or whose `close` is never invoked) leaks the fd. The socket is created with no timeout. This is Phase-4 prep / not on the live loop today, hence HIGH not CRITICAL, but it's a latent fd leak the moment SITL is wired in.
- **Blast radius:** fd/socket leak under real SITL use; benign today (dry-run default).

---

## MEDIUM

### M1 — SQLite store opens a brand-new connection on every read/write
- **File:** `dexia/ontology/store.py:35-39, 87-200`
- **What's wrong:** Every `append_lineage`, `write_snapshot`, `recent_lineage`, `drone_history` does `sqlite3.connect(...)` then `close()`. WAL + `check_same_thread=False` make this *correct* across the streamer and uvicorn threads, but connection-per-call is wasteful on the hot path (the streamer writes a snapshot every 10 ticks and the API reads lineage per request). The in-process `_WRITE_LOCK` serializes writes, so a slow snapshot write blocks every API-thread write for its duration. `_INITIALIZED` is a module-global set guarded by nothing — two threads first-touching a fresh DB can both run `_ensure_schema` (idempotent `CREATE IF NOT EXISTS`, so harmless, but it's an unguarded shared-state read/write).
- **Blast radius:** Minor latency/throughput; not a correctness bug given WAL + idempotent schema.

### M2 — `telemetry.json` / `ontology_state.json` are read by the API mid-write with no shared lock
- **Files:** `telemetry_stream.py:64-93, 480`, `sim_api.py:209-218, 296-303`, `dexia/runtime/health.py:50-72`, `dexia/runtime/evals_worker.py:29-36`
- **What's wrong:** The streamer writes via atomic `os.replace` with retry (good), but the FastAPI `telemetry()`/`_load_ontology()`, `HealthMonitor.sim_status`, and `evals_worker` all `open().json.load()` with no coordination. The atomic replace mostly protects readers, but the streamer's own writer *skips the frame* after 8 retries (`telemetry_stream.py:83-84`) — under sustained reader contention telemetry silently goes stale for a tick while `os.replace` keeps losing the race, which the HealthMonitor could (briefly) read as a stall. Readers catch parse errors and return 503/`read-failed`, so no crash, but freshness is best-effort.
- **Blast radius:** Occasional stale/late telemetry frame and a possible false stall flag under heavy concurrent reads; self-heals next tick.

### M3 — Silent exception swallowing hides real failures along the persistence path
- **Files:** `telemetry_stream.py:483-485, 160-165`, `dexia/runtime/config.py:133-135, 140-142`, `dexia/api/llm_gateway.py:162-166`, `dexia/ontology/store.py:178` (broad `except Exception` around the json_each fast path)
- **What's wrong:** `lineage_store.write_snapshot` failures are caught and dropped (`except Exception: pass`) — the queryable ontology history can silently stop persisting with no signal. `config.load_config` swallows YAML parse errors and silently falls back to defaults, so a typo in `dexia.config.yaml` (e.g. a bad threshold or wrong principals block) is invisible: the operator thinks their override applied; it didn't, and auth quietly reverts to the C1 default keys. `DualSink.publish` and `_audit` swallow all sink/IO errors.
- **Blast radius:** Misconfiguration and persistence failures are undetectable until something downstream is empty/wrong; directly weakens the auth and audit guarantees (a malformed `auth:` block silently re-enables the default credentials).

### M4 — `_enqueue` retry math and the 503 path can still surface 500s; no request size/rate limits on the API
- **Files:** `sim_api.py:177-198`, all `@app.post` endpoints
- **What's wrong:** `_enqueue` only retries `PermissionError`; any other `append_command` exception is an immediate 500 (acceptable), but the governed path (`_govern_enqueue`) does the ActionBus submit + SQLite lineage write *before* the enqueue, so an enqueue 503 leaves a lineage row recorded for a command that never queued (lineage/command divergence). No endpoint has rate limiting, body-size caps, or auth throttling, so the known default key (C1) plus unlimited `deploy` calls can exhaust the object pool / spam the queue freely.
- **Blast radius:** Lineage can claim a command was issued that was never enqueued; trivial DoS of the command queue.

### M5 — `dexia.db` (16 MB) is present in the working tree and bind-mounted into containers; not git-clean
- **Files:** root `dexia.db` (16 MB), `docker-compose.yml:28,43,62,83` (`.:/app` / `.:/host` mounts), `.gitignore:41-43`
- **What's wrong:** The DB is gitignored (good) but a 16 MB populated `dexia.db` plus `-wal`/`-shm` sit in the repo root and are bind-mounted read-write into every Python service. All four containers share one SQLite file over a bind mount; SQLite over certain bind-mount/host-fs combinations (Docker Desktop on Windows/macOS especially) is a documented source of `database is locked` / WAL corruption. The same goes for `commands.json`/`telemetry.json` shared across the Node and Python containers via the mount.
- **Blast radius:** Cross-container SQLite over a bind mount risks `database is locked`/WAL issues on Docker Desktop; stale committed-adjacent DB state can leak into a "fresh" run.

---

## Notes / lower-priority

- `sim_api.py:135-142` `_live_registry()` rebuilds a registry from the on-disk ontology snapshot on **every governed write** (deploy/activate/recall) — an `open()+json.load()+parse` per mutating request on the hot path.
- `auth.py` has no constant-time comparison and no notion of key rotation/expiry; keys are plaintext dict lookups.
- `requirements.txt` comments say MuJoCo/Ray are "not required for Phase 1," but `Dockerfile.sim` installs them and `telemetry_stream.py` imports `DroneMARLEnv` at module top → the streamer hard-depends on the heavy stack despite the lightweight framing.
- `command_server.py` has no `/health` endpoint and is absent from `docker-compose.yml` entirely, so the Operations-UI backend is not part of the declared "5-service stack" — deployment story for it is undefined.
