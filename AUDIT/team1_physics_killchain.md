# Team 1 Audit — Physics Dynamics & Kill-Chain

Scope: `dexia/physics/`, `dexia/physics3d/`, `dexia/wargame/anti_air.py`,
`dexia/fusion/effects.py`, `dexia/fusion/motion.py`, `dexia/comms/gilbert_elliott.py`,
`dexia/domain_randomization/wind.py`, root drivers (`scenario_killchain.py`,
`train_phase3.py`, `eval_phase3.py`), `equipment_catalog.yaml`, `drone_profiles.json`.

Method: read every in-scope module, traced the live call chains
(`DroneMARLEnv.step` → `AntiAirBattery` / detection / kill, and
`WorldState.step` → `MotionModel` → physics3d engines, and
`EffectResolver` → `engage_air` → `MissileEngine`), and numerically reproduced
the key geometry/numerics in the 3.12 venv.

---

## ROOT-CAUSE INSIGHT (read this first)

**There are two physically incompatible "worlds" stapled together, and the kill
chain that is actually wired into the game lives entirely in the small one.**

- The **live game** is `DroneMARLEnv` (`dexia/envs/drone_marl_env.py`). It runs on
  a **toy metre scale**: target at `[5,5,1]`, detection radius `4.0 m`, altitude
  floor `2.5 m`, strike radius `0.7 m`, and the air-defense threat
  `AntiAirBattery` with `radar_range=8.0 m`, `kill_radius=1.5 m`. MuJoCo drones
  are ~0.6 kg at sub-metre arm length. This is the world the trained policy,
  `scenario_killchain.py`, `train_phase3.py`, and `eval_phase3.py` all exercise.

- The **tier-B / P4 physics3d world** (`FixedWing3DOFEngine`, `MissileEngine`,
  `engage_air`, `sam_can_engage`, the SA-11/S-300 catalog) runs on a **real
  military scale**: aircraft at **1500 m AGL**, 45–200 m/s, SAMs with 28–75 km
  range and 15–25000 m altitude envelopes. This world is **only reachable from
  unit tests and demo `main()` blocks** — `engage_air`/`sam_can_engage` are
  explicitly *not wired into `MissionRunner`* (see `effects.py:238-241`).

The consequence is the headline backlog bug. The detection/kill gates in the
small world are **single fixed-radius spheres referenced to a target at z=1 m**,
so the moment an air vehicle gains altitude (to survive, to climb, under wind),
it leaves the sphere and the chain can never complete. The two worlds never
reconcile their units, frames, or scales — every defect below is a symptom of
that split or of the small-world geometry that the split forces the game to use.

---

## CRITICAL

### C1 — "Air altitude blocks winnable game": detection & kill gates are altitude-collapsing spheres
**File:** `dexia/envs/drone_marl_env.py:326-328` (`_is_detection`), `:599` (kill test),
geometry constants at `:191-197`.

```python
def _is_detection(self, st) -> bool:
    dist = float(np.linalg.norm(st.position - self.target))   # full 3D distance
    return dist <= self.detection_radius and st.position[2] >= self.los_min_altitude
```

`target = [5,5,1]`, `detection_radius = 4.0`, `los_min_altitude = 2.5`. The test
demands the recon be **inside a 4 m sphere around a z=1 target** *and* **at
altitude ≥ 2.5 m**. Those two constraints fight: the higher the recon flies, the
larger the vertical component of `dist`, the smaller the horizontal disc of valid
positions. Reproduced numerically:

| recon altitude | vertical gap to target | max horizontal reach into sphere |
|---|---|---|
| 2.50 m | 1.50 m | 3.71 m |
| 3.50 m | 2.50 m | 3.12 m |
| 4.00 m | 3.00 m | 2.65 m |
| 4.85 m | 3.85 m | 1.09 m |
| **5.00 m** | **4.00 m** | **0 (no solution)** |

At altitude ≥ 5 m **detection is geometrically impossible**. The kill gate
(`:599`, `strike_radius = 0.7` to the same z=1 target) is far worse — a 0.7 m
sphere a kamikaze must enter while diving. Because `_broadcast` is **de-latched
every step** ([결함 10] at `:589-591`) and a kill requires `_broadcast` true on
the *same* tick a kami is within 0.7 m (`:595-602`), any altitude excursion both
suppresses the broadcast and shrinks the only volume where a kill can register.
**Blast radius:** the entire recon→broadcast→strike→kill objective is
unwinnable whenever drones operate above ~5 m; reward `w1`/`w2` (detection/kill
events) can never fire, so MARL training receives no positive kill-chain signal.
This is the backlog bug, and its real cause is the toy geometry, not a tunable.

### C2 — The real air-defense kill chain (`engage_air`) is dead code; the live AA can't reach an air target
**File:** `dexia/fusion/effects.py:238-265`, `dexia/wargame/anti_air.py:82-104`,
`dexia/envs/drone_marl_env.py:235-244`.

The only air-defense actually stepped by the game is `AntiAirBattery`, configured
with `radar_range=8.0`, `kill_radius=1.5`, `zone_ttl=4` — **metres/seconds on the
toy scale**. The catalog's real SAMs (`sa11_sam` range 28 km, `s300_sam` 75 km)
and the proper range∧altitude∧LOS gate + PN intercept in `engage_air` /
`sam_can_engage` are **never called from any runner** (the module-level comment at
`effects.py:238-241` admits "Kept as module functions (not wired into
MissionRunner)"). So in the live game an air vehicle at 1500 m AGL is untouchable
by the SAM logic that was actually written for it, and the AA that *is* wired in
operates at an 8 m radar range it can only satisfy on the toy map. **Blast
radius:** there is no functioning air-defense engagement against realistic air
targets anywhere in the executed code; all the SAM physics is unverified-in-situ.

### C3 — `MissileEngine` PN: unbounded lateral accel + speed renorm = non-physical "always turns instantly," yet misses fast crossers
**File:** `dexia/physics3d/missile.py:33-42`, `dexia/physics3d/guidance.py:51-65`.

```python
a = proportional_navigation(r_rel, v_rel, self.vel, self.N)
self.vel = self.vel + a * dt          # a is UNbounded (no a_max / structural limit)
sp = np.linalg.norm(self.vel)
if sp > 1e-9:
    self.vel = self.vel / sp * self.speed  # renormalize back to constant speed
```

Two coupled errors: (1) the PN command has **no acceleration/​load-factor cap**, so
`vel + a*dt` can rotate the velocity by any angle in one step, then renormalization
hides the energy violation — the missile behaves like it can turn instantly (no
real missile can). (2) Despite that, against a genuinely fast crossing target the
Euler step + early-break still misses badly: reproduced a **4845 m miss** vs a
3000 m/s crosser. So the model is simultaneously over-capable (instant turns) and
silently wrong (huge misses) depending on geometry. **Blast radius:** any `hit`
verdict from `engage_air` (`effects.py:265`, `miss <= lethal_r`) is not a valid
intercept result; if C2 were ever fixed and this wired in, SAM lethality would be
arbitrary.

### C4 — `intercept()` closest-approach loop breaks early and over-counts hits
**File:** `dexia/physics3d/missile.py:44-60`.

```python
if d > miss + 50.0:   # past closest approach
    break
```

The "past closest approach" heuristic uses a **fixed 50 m slop** regardless of
missile speed (800 m/s ⇒ 40 m per `dt=0.05` step) and target dynamics. For a
maneuvering or receding target the separation can rise by >50 m in a single step
*before* a true minimum, terminating the search early and returning an inflated
`miss`; conversely it can mark a transient near-pass as the global minimum.
Combined with C3 this makes `hit` neither sound nor complete. **Blast radius:**
SAM Pk in the tier-B path is decided by an unreliable scalar.

---

## HIGH

### H1 — Quaternion/Euler convention is inconsistent across the physics layers
**File:** `dexia/physics3d/state.py:18-38` (ZYX, body→world) vs
`dexia/physics/mujoco_engine.py:80-94` (`_quat_to_euler`) and the MuJoCo model
`mju_euler2Quat(..., "xyz")` at `:188`.

`state.py::quat_from_euler` builds a **ZYX** quaternion and documents
"body→world"; `physics/base.py` `DroneState6DOF` documents Euler as
"world/ZYX convention," while the MuJoCo path resets attitude with an **"xyz"**
Euler sequence (`mujoco_engine.py:188`) and reads it back with a hand-rolled
converter. ZYX and XYZ intrinsic sequences are **not equal** for non-trivial
attitudes, so attitudes round-tripped or compared across the two engines diverge.
`Body6` is declared as the "one shape every motion model writes" (`state.py:1-7`),
but it is fed by both conventions. **Blast radius:** any consumer that compares
attitude across the MuJoCo 6-DOF engine and the physics3d engines (HUD overlays,
stability penalties, fusion) sees silently wrong roll/pitch; only yaw-symmetric
tests pass.

### H2 — `FixedWing3DOFEngine` bank quaternion uses `roll = -bank`, contradicting the JSBSim engine it is meant to be interchangeable with
**File:** `dexia/physics3d/air.py:127` vs `dexia/physics3d/jsbsim_engine.py:115`.

`air.py` writes `quat=quat_from_euler(-self.mu, self.gamma, self.chi)` (roll =
*minus* bank). `jsbsim_engine.py` comments the opposite sign relationship
(`desired_phi = -bank`, "+phi (right wing down) turns ENU course CW"). The two
engines are explicitly declared API-interchangeable behind `make_air_engine`
(`jsbsim_engine.py:155-165`), but they emit **opposite roll signs** for the same
maneuver. **Blast radius:** swapping a hero aircraft from numpy to JSBSim flips
its visual/where-stored bank direction; any roll-dependent logic (gimbal pointing,
sensor footprint, HUD) inverts.

### H3 — ENU vs NED / compass-vs-course frame juggling in JSBSim engine is fragile and only partly applied
**File:** `dexia/physics3d/jsbsim_engine.py:76-105, 139-151`.

The engine mixes JSBSim's **NED + compass heading (0=N, CW)** with the project's
**ENU + course (0=E, CCW)**. `reset` converts heading once
(`psi-true-deg = (90 - deg(heading)) % 360`, `:77`), `_sync` derives `chi` from
`atan2(v_north, v_east)` (`:105`), and `state()` flips vertical via
`vu = -v_down` (`:143`). But `state()` returns the **raw JSBSim Euler yaw**
(`yaw = attitude/psi-rad`, `:146`) into `quat_from_euler` — i.e. a **compass yaw
fed into an ENU-course quaternion builder**, so the stored attitude yaw is in a
different frame than `self.chi` and than `FixedWing3DOFEngine`. Position east/north
are taken from `distance-from-start-lon/lat-mt` (`:98-99`) which are themselves
NED-ish; no consistent single transform is applied. **Blast radius:** hero-aircraft
attitude and ground track disagree; LOS / pointing computed from the quaternion
are wrong by the ENU↔NED rotation. Latent because JSBSim is an optional dep that
usually falls back to numpy (`:160-165`), hiding the bug in CI.

### H4 — Engine `dt` mismatch: physics3d motion sub-steps at world rate, but missile/ballistic use their own fixed `dt`
**File:** `dexia/fusion/world.py:84,146-151` (`physics_hz=20` ⇒ sub `dt=0.05`),
`dexia/physics3d/missile.py:45` (`dt=0.05`, `t_max=30`),
`dexia/physics3d/ballistic.py:57-72` (`dt=0.25`).

`WorldState.step` sub-steps motion models at `1/physics_hz = 0.05 s`, but the
ballistic arc integrates at `dt=0.25 s` and the missile intercept at its own
`dt=0.05 s` decoupled from the world clock and capped at `t_max=30 s`. A
600–800 m/s missile advances 30–40 m per step; a 0.25 s ballistic step is 50+ m of
travel for a shell — coarse enough that the RK4 arc and the impact tick
(`effects.py:90`, `tick + round(tof_s)`) can disagree on where/when the round
actually is. There is no shared timebase. **Blast radius:** HUD trajectory vs
logged impact tick desync; intercept results depend on an arbitrary internal
step, not the sim rate.

### H5 — `AntiAirBattery` ages zones in seconds using a `dt` that is decoupled from the env's true step time
**File:** `dexia/wargame/anti_air.py:99-100,153-159`, used at
`drone_marl_env.py:235-244` (constructed **without** passing `dt`).

`AntiAirBattery.__init__` defaults `dt=0.02` and ages `ThreatZone.ttl` by that `dt`
each `update()` (`anti_air.py:157`). The env constructs the battery (`:235-244`)
**without** forwarding the MuJoCo engine `dt` (which is `0.004*decimation`; here
0.02 by luck of `control_decimation=5`, but it changes the instant decimation is
tuned). `fire_cooldown` is meanwhile an **integer step count** (`:97,178`), not a
time — so cooldown is Hz-dependent while TTL is Hz-independent, and the two will
drift apart under any dt change. The comment claims "Hz-independent" but only the
TTL path is. **Blast radius:** retune the control decimation and AA lethality
windows silently change; the anti-tunneling guarantee (결함 6) is unaffected but
the zone lifetime is.

### H6 — Gilbert-Elliott "rate vs probability" reinterpretation silently corrupts legacy configs
**File:** `dexia/comms/gilbert_elliott.py:96-101,143-166`.

The constructor now treats `p_good_to_bad` / `p_bad_to_good` as **continuous-time
rates [1/s]** and computes `p_switch = 1 - exp(-rate*dt)` (`:159,163`). But the
defaults (`0.05`, `0.40`) and any caller passing the historical **per-step
probabilities** are now read as rates. At `dt=0.02` a legacy `p=0.40`
probability becomes `1 - exp(-0.40*0.02) ≈ 0.008` per step — a **50× drop** in
switching frequency. The docstring at `:97-99` acknowledges this ("under-estimate
the rate") but ships it anyway. **Blast radius:** comms burstiness in
`DroneMARLEnv` (which gates broadcast via `link_good`, `:560-583`) is far more
optimistic than configured; the `network_survivability` reward term and broadcast
suppression no longer match any documented channel model. `stationary_bad_probability`
(`:196-203`) is fine (ratio cancels), masking the per-step error.

---

## MEDIUM

### M1 — Gravity is unconditionally compensated, so the 3-DOF backend models no vertical dynamics
**File:** `dexia/physics/kinematics_3dof.py:86-89`. `gravity_compensation=True`
default zeroes `f_gravity` every step, so altitude responds only to commanded
thrust with no weight to fight. Combined with C1 this means the "altitude" the
detection gate keys on is essentially free to set — yet the gate still punishes
it. The two design choices are mutually inconsistent.

### M2 — `mujoco_builder` dead/oxidized hub-size line
**File:** `dexia/physics/mujoco_builder.py:151-152`. `hub_hx` is assigned
(`hub_r * 0.8` … via `hub_hy` line) then **immediately overwritten** on the next
line (`hub_hx = hub_r * (1.3 if tandem else 0.8)`); line 151 also sets `hub_hy`
using a `tandem` test but the intended longer-in-x geometry is only half-applied.
Cosmetic (visual geom only, mass is in `<inertial>`), but it is a copy-paste bug
that signals the inertia/visual derivation was not reviewed.

### M3 — `effects._do_strike` recomputes `terrain` twice and arcs in metres on the toy scale
**File:** `dexia/fusion/effects.py:123,147` (`terrain = getattr(...)` twice), and
the hard-coded `arc = sin(f*pi)*200.0` "cruise altitude arc" (`:156`) and
`capture = lethal_r + 120.0` (`:130`) are **fixed metre constants** that are
meaningless on the toy `[5,5,1]` scale and oversized on it. Same fixed-120 m
seeker capture appears in `mission_manager.py:502`. Indicates the effect geometry
was tuned only for the km-scale world, never reconciled with the game scale.

### M4 — `BallisticEngine.solve` lob ignores drag in the launch solution
**File:** `dexia/physics3d/ballistic.py:26-38`. The launch velocity is the **vacuum**
solution (`v_z = (dz + 0.5 g tof²)/tof`), then drag is applied during flight
(`_f`, `:40-42`). So the flown arc systematically **undershoots** the aimpoint by
the drag deficit; the docstring even concedes the kill is decided by the aimpoint,
not the flown round — i.e. the trajectory shown on the HUD does not actually reach
where the round is scored to land. Honest-verification concern: the visual and the
scored outcome diverge.

### M5 — `WindField` ambient default is a constant +x breeze that biases every episode
**File:** `dexia/domain_randomization/wind.py:74-78`, env override
`drone_marl_env.py:162` (`[0.6,0.2,0.0]`). The mean wind is a persistent
directional force, not zero-mean; with gravity compensation on (M1) this is an
uncompensated constant horizontal push that the toy-scale detection sphere (C1)
cannot tolerate — another mechanism by which "the drone drifts out of the 4 m
sphere and the chain dies."

---

## Cross-cutting summary (the tier boundary)

The P3/P4/P5 file boundary is *physically* clean (no imports leak upward; physics3d
does not import the env), but it is **semantically broken**: the two tiers model
the same entities at incompatible scales and frames and never exchange a validated
state. `Body6` is advertised as the universal hand-off type (`state.py`,
`physics3d/__init__.py`) yet is populated by ZYX, XYZ, ENU-course, and raw-JSBSim-
compass conventions (H1–H3). The kill chain the game runs (C1) is not the kill
chain the physics was written for (C2–C4). Fixing the backlog bug requires
choosing one world: either scale the live `DroneMARLEnv` geometry up to the
physics3d world and wire `engage_air` in, or explicitly down-scale and re-verify
the SAM/air models for the toy world. Patching `los_min_altitude`/`detection_radius`
alone will not make it winnable while the gate remains a single target-referenced
sphere.
