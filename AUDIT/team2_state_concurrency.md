# Team 2 Audit — State Consistency, Atomicity, Race Conditions

Scope: `dexia/ontology/*`, `dexia/fusion/{engine,world,feeds}.py`,
`dexia/integrations/{command_queue,webhook}.py`, plus the cross-process call
chain that actually exercises them (`telemetry_stream.py`, `dexia/api/sim_api.py`).
Method: traced real call chains (ActionBus → store → SQLite/JSONL; FastAPI
`_govern_enqueue` → `command_queue`; streamer → `ontology_state.json` → FastAPI
reader). Findings ordered by severity.

Date: 2026-06-21.

---

## CRITICAL

### C1. ActionBus governance validates against a STALE / cross-process ontology snapshot — TOCTOU on every guarded action
**Files:** `dexia/api/sim_api.py:135-173`, `dexia/ontology/action_bus.py:98-111`,
`telemetry_stream.py:478-480`, `dexia/ontology/serializer.py:171-190`

The only state the ActionBus guards (`_not_lost`, `_recallable`, `_kami_mac`)
read is a registry rebuilt **per request** from `ontology_state.json`:

```
_live_registry() -> registry_from_snapshot(_load_ontology())   # sim_api.py:135-142
bus = ActionBus(_live_registry(), store=get_store())            # sim_api.py:159
```

That file is written by a *different process* (the streamer) once per tick
(`telemetry_stream.py:480`). There is **no lock shared between the streamer's
write and the API's read** — `_load_ontology()` is a bare `open()/json.load()`
(`sim_api.py:296-301`). Consequences:

1. **Stale-state authorization.** A drone goes `lost` in the sim at tick *N*;
   the snapshot file is only refreshed at the next `_atomic_write_json`. Between
   the truth changing and the file being rewritten (plus the API reading
   whatever the last frame wrote), `engage`/`move` on a dead drone is *approved*
   and a side-effect command is enqueued. The guard enforces a belief that is
   already false. Inversely, an action can be wrongly *denied* on stale "lost".
   This is the exact kill-chain safety property the funnel claims to enforce.
2. **MAC bypass window.** `_kami_mac` (`actions.py:72-83`) reads
   `missions[0].broadcast`. `broadcast` is recomputed every tick from events and
   the snapshot reflects only the last write. A kamikaze `engage` can be
   authorized/denied against a broadcast flag that no longer matches ground
   truth.
3. **No atomicity across read-modify-decide.** Even within one request the bus
   reads the registry, validates, then enqueues a command with zero coupling to
   the world state the streamer will act on. By the time the streamer drains the
   command (`telemetry_stream.py` next tick), the gating condition may be
   inverted. Validation and effect are fully decoupled in time and process.

**Blast radius:** every governed write (`deploy/recall/engage/move/...`). The
"single enforced write funnel" is enforced against a snapshot that is both stale
and read without synchronization — the central safety claim of the system is
unsound under any concurrency.

---

### C2. `commands.json` cross-process lock is best-effort and silently degrades to NO lock — lost updates on the read-modify-write
**File:** `dexia/integrations/command_queue.py:109-151, 217-242`

`_file_lock` is documented and built as best-effort: if the advisory OS lock
cannot be acquired within `_LOCK_ACQUIRE_TIMEOUT` (2 s) it **`break`s and
proceeds anyway** (`command_queue.py:138-139, 116-117`). Both `append_command`
and `drain_commands` then perform a full read-modify-write (`_read` → mutate →
`_atomic_write`) believing they are serialized when they may not be.

The re-read in `drain_commands` (lines 239-241) only protects against *appends
that land after the snapshot*; it does **not** protect a concurrent
`append_command` whose own read-modify-write straddles the drain's
`_atomic_write`. Classic lost-update: writer reads `[A]`, drain reads `[A]`,
writer writes `[A,B]`, drain writes `[]` (it saw only A, re-read may still race
the writer's rename) → **command B is dropped with no error**. The concurrency
test (`test_command_queue_concurrency.py`) only asserts "no WinError 5 and
`drained==appended`" under a benign 4-writer/1-reader/10 Hz load where the lock
*usually* succeeds; it does not exercise the degraded (lock-timeout) path, so
the loss mode is untested.

**Windows-specific aggravator:** `msvcrt.locking(LK_NBLCK, 1)` locks **1 byte**
of a sidecar `.lock` file that is opened fresh (`open(lock_path, "a+")`) on every
call (line 125). This is a mandatory byte-range lock, but the data file
`commands.json` itself is never locked — only the sidecar — so any writer that
does not go through `_file_lock` (see C3) is completely unsynchronized.

**Blast radius:** dropped deploy/recall commands under burst; the "never lose a
command" contract the module advertises is not guaranteed.

---

### C3. The Node `/api/command` writer does NOT share the Python lock → unprotected concurrent writer to `commands.json`
**File:** `dexia/integrations/command_queue.py:31-39` (docstring admits it),
plus the HUD `dexia-hud` Next.js `/api/command` route (out of Python scope but
named in the contract).

The module's own docstring (lines 31-36) states the Node route "does not share
our lock." The entire locking scheme (C2) only serializes the *Python* writer
against the *Python* reader. The HUD appends to the same `commands.json` with an
independent code path and no participation in `commands.json.lock`. So:

- Node append + Python `drain_commands` interleave with **only** the
  `os.replace` retry (C2's "layer 2") as protection — a retry on
  `PermissionError` is not mutual exclusion; it does not prevent two
  read-modify-write cycles from clobbering each other's result, only prevents
  the rename from erroring.
- A Node writer reading `[A]`, a Python drain writing `[]`, then Node writing
  `[A,B]` re-adds an already-drained command **A** → the streamer can
  **double-process** a spawn/remove. No idempotency key check exists on the
  drain side (`drain_commands` removes by `id` but never dedupes re-introduced
  ids against already-consumed ones — there is no consumed-set persistence).

**Blast radius:** duplicate drone spawns / double removals, or dropped commands,
whenever the HUD and the streamer touch the queue concurrently — i.e. normal
operation.

---

## HIGH

### H1. Audit divergence: SQLite lineage and JSONL audit can disagree; `command` payload is in JSONL but not the DB, and a swallowed DB error drops the action silently
**File:** `dexia/ontology/action_bus.py:78-95, 114-119`

The bus claims "either both written or neither" (lines 79-81). It is not
atomic:

- `append_lineage` commits to SQLite, **then** `_audit()` appends JSONL
  (lines 83-88). If the process dies between the DB commit and the JSONL write,
  the DB has the row and the JSONL does not — divergence in the documented-
  consistent direction.
- The DB row stores `status`/`reason` but **never the resulting command**
  (`store.append_lineage` takes `command_id` but the bus passes none —
  `action_bus.py:83-87` omits it; `command_id` is always NULL in the `lineage`
  table). The JSONL record *does* carry `record["command"]`. So the two audit
  trails contain different information; reconstructing "what command did this
  accepted action enqueue" from the DB is impossible. The `command_id` from the
  actual enqueue (`sim_api.py:171`) is never written back to lineage either, so
  lineage cannot be joined to the command queue at all.
- On any DB exception the whole record is dropped: `except Exception: lineage_id
  = None` and **JSONL is deliberately skipped** (lines 89-91). An accepted,
  state-changing action whose command is *still enqueued downstream*
  (`_govern_enqueue` only checks `result["status"]`, not lineage success —
  `sim_api.py:162-171`) leaves **no audit record at all** while still mutating
  the world. Silent governance gap.

**Blast radius:** the immutable provenance trail (the AIP selling point) is
neither complete nor joinable; an action can execute with zero audit.

### H2. SQLite opens a brand-new connection per call but serializes ALL writes on one process-global lock; cross-process write contention is unhandled
**File:** `dexia/ontology/store.py:35-38, 87-117, 27`

- Every read/write does `sqlite3.connect(... timeout=5.0)` and closes it
  (lines 92-104, 121-129). Under WAL this is correct-ish, but `_WRITE_LOCK`
  (line 27) is a **per-process** `threading.Lock`. The streamer process and the
  FastAPI process each hold their *own* `_WRITE_LOCK`; the lock gives zero
  cross-process protection. Cross-process write serialization relies entirely on
  SQLite's own file lock with a 5 s `timeout`. Under sustained snapshot writes
  (`write_snapshot` every 10 ticks) + per-request lineage inserts, a writer can
  hit `database is locked` after 5 s and raise — which in the bus path
  (H1) is swallowed and the action vanishes from audit.
- `check_same_thread=False` (line 36) plus the shared module-global `_STORE`
  singleton (`store.py:213-223`) means the same `LineageStore` instance is used
  from many uvicorn worker threads. Methods open their own connection so this is
  *mostly* safe, but `get_store()` mutates the global `_STORE` without a lock
  (lines 219-222): two threads racing the first `get_store(db_path)` can each
  build a `LineageStore` and one wins arbitrarily — benign here, but it is an
  unsynchronized global-singleton init.

**Blast radius:** `database is locked` exceptions under load → swallowed audit
loss (compounds H1); no cross-process write coordination despite the design
comment claiming WAL "handles cross-process concurrency" (it handles readers vs
a writer, not contended writers, gracefully within a fixed timeout).

### H3. `ontology_state.json` writer can skip frames silently; reader has no staleness guard → unbounded stale state feeding governance
**File:** `telemetry_stream.py:64-94, 480`; `dexia/api/sim_api.py:296-308`

`_atomic_write_json` retries `os.replace` 8× then **`return`s without writing**
(lines 83-84: "skip this frame's write"). No error, no timestamp, no sequence
number is recorded. The FastAPI reader `_load_ontology()` (sim_api.py:296-301)
has **no freshness check** — it will happily serve and *govern against* a
snapshot that is many ticks (or, if the streamer stopped, arbitrarily) old.
`HealthMonitor` is wired to `telemetry.json` (sim_api.py:77), not to
`ontology_state.json`, so ontology staleness is invisible. Directly compounds
C1: the stale-snapshot governance has no upper bound on staleness and no signal
that it is stale.

**Blast radius:** governance decisions on indefinitely old world state with no
detection.

### H4. FusionEngine: unbounded memory growth + `_next` id counter is not reset/persisted; stale tracks never pruned from the list
**File:** `dexia/fusion/engine.py:166-198, 218-220`

- `self.tracks` only ever grows: tracks transition to `status == "stale"`
  (line 197) but are **never removed** from `self.tracks`. `active_tracks()`
  filters them out of views, but `update()` iterates the full list every tick
  (lines 191-196) and `_associate` skips stale ones (line 203) — so the list
  grows without bound over a long episode. Memory leak + O(n) per-tick cost that
  degrades as the run continues.
- `_next` (line 177, 218-220) is an in-memory monotonic counter. The engine is
  not persisted/round-tripped, so on any restart track ids restart at `TRK-…-001`
  and **collide** with previously emitted ids in any downstream store that keyed
  on them (snapshots, OAG context). No uniqueness across process lifetime.

**Blast radius:** long-run memory growth; track-id collisions across restarts
corrupt any consumer that treats `track_id` as a stable key.

---

## MEDIUM

### M1. `_atomic_write` is not crash-atomic (no fsync) — a power/crash mid-write can leave `commands.json` truncated/empty
**File:** `dexia/integrations/command_queue.py:199-211`; same pattern
`telemetry_stream.py:64-94`

`tempfile.mkstemp` + `json.dump` + `os.replace` gives *rename* atomicity but
never `f.flush()/os.fsync(fd)` before the close/replace, and never fsyncs the
directory. On a crash the rename can land while the temp file's bytes are still
in the OS cache → reader sees a 0-byte or partial file. `_read` treats a
truncated file as malformed and returns `[]` (lines 173-175), which for
`commands.json` silently discards every queued command. "Atomic" here means
"reader never sees a half-write under normal operation", not durable.

### M2. `drain_commands` drops malformed commands lacking an `id`, and dedupes by `id` where `id` may be `None`
**File:** `dexia/integrations/command_queue.py:237-242`

`processed_ids = {c.get("id") for c in pending ...}` — any command without an
`id` contributes `None` to the set; `remaining = [c for c in current if
c.get("id") not in processed_ids]` then drops **every** id-less command in
`current` (they all match `None`). A single malformed/legacy command poisons the
filter. Node-side or hand-written commands without ids are silently lost.

### M3. `registry_from_snapshot` silently swallows construction failures → partial registry used for governance
**File:** `dexia/ontology/serializer.py:182-189`

`except TypeError: pass` drops any object whose snapshot dict has a field the
dataclass doesn't accept (schema drift between writer and reader versions). The
governance registry is then built from a *subset* of the world — e.g. a renamed
`DroneObject` field makes the drone vanish from the registry, so `_not_lost`
finds `drone is None` and **permits** the action it should have blocked. Schema
versioning drift turns into a silent authorization bypass. There is no schema
version field anywhere (`schema.py`) to detect the drift.

### M4. `_kami_mac` reads `missions[0]` unconditionally — ordering/duplicate dependence
**File:** `dexia/ontology/actions.py:78-82`

`broadcast = missions[0].broadcast if missions else False`. The MAC trusts
mission index 0. `registry.all("MissionObject")` returns dict-values order; if
more than one mission object ever exists (the snapshot path replaces with a
single `mission_0`, but `InMemoryRegistry.replace` does not enforce
singularity), the guard reads an arbitrary one. Coupled with C1's stale source,
the kill-chain MAC rests on `missions[0]` of a stale, order-dependent set.

### M5. `objects_from_telemetry` vs `parse_telemetry_to_ontology` diverge (two non-equivalent serializers) and lose fields on round-trip
**File:** `dexia/ontology/serializer.py:26-75 vs 78-151`

Two parallel telemetry→ontology paths exist with different defaults and field
coverage: `parse_…` sets `kill_radius`/`ew_range`/`loss_reason` differently and
keys threats as `aa_{i}`, while `objects_from_telemetry` hardcodes `aa_0`,
omits `kill_radius`, and computes `active_zones` differently. Whichever the
caller picks changes the object graph, and the `ontology_state.json` round-trip
(`registry_from_snapshot`) can only reconstruct fields present in the dict — any
field not emitted by `to_dict()`/added later is lost. The observed
`ontology_state.json` already shows `kill_radius: 0.0` and `active_zones: 0`
where the source telemetry had real values, evidence of lossy/divergent
serialization.

### M6. `webhook` swallows every failure including programming errors; fire-and-forget means lost events are invisible
**File:** `dexia/integrations/webhook.py:32-54, 80-85`

`_post` catches bare `Exception` (line 53) and only `log.debug`s it; a malformed
payload or a bug serializes to a dropped event with no surfaced error. Combined
with `_executor.submit` swallowing `RuntimeError` on shutdown (lines 82-85),
events emitted during interpreter teardown are dropped silently. Acceptable for
telemetry, but if any governance/audit event is routed here it disappears with
no trace.

---

## Most dangerous single hole

**C1 + H3 together.** The "single enforced write funnel" authorizes every
state-changing action against `ontology_state.json`, a file written by a
separate process once per tick and **read without any shared lock or freshness
check**. The streamer may skip the write entirely after 8 failed `os.replace`
retries (silently), and the reader has no staleness bound. So the system's core
safety guarantees — "a LOST drone cannot be commanded", "a kamikaze cannot
engage before broadcast" — are decided on stale, unsynchronized, possibly
arbitrarily-old state, with the validation fully decoupled in time and process
from the command it gates. Under any real concurrency the funnel can both
approve actions it should deny and deny actions it should approve, and there is
no signal that it happened.
