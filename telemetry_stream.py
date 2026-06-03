"""Phase-5 telemetry streamer: DroneMARLEnv -> telemetry.json (or Redis).

Runs the swarm environment (with the Anti-Air battery enabled) in a loop and
serializes the current full state each tick so the Next.js HUD can poll it.

Sink selection:
  * If ``redis`` is importable AND a local Redis server answers, telemetry is
    published to a Redis key/channel.
  * Otherwise it falls back to writing ``telemetry.json`` every tick (atomic
    temp-file replace), so there is zero setup overhead.

To keep the demo legible (and deterministic), this drives a *scripted* tactical
scenario rather than a trained policy:
  * recon_0 climbs to the observation point above the target -> triggers the
    detection BROADCAST;
  * after the broadcast, kami_0 runs in toward the (AA-defended) target and is
    intercepted by the Anti-Air battery -> a LOSS event the HUD's AI Staff
    reacts to.
All other drones hold station.

Run:
    .venv312\\Scripts\\python.exe telemetry_stream.py            # stream forever
    .venv312\\Scripts\\python.exe telemetry_stream.py --ticks 50 # stream N ticks
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone

import numpy as np

from dexia.envs.drone_marl_env import DroneMARLEnv, RECON_PREFIX
from dexia.integrations.command_queue import drain_commands
from dexia.ontology import InMemoryRegistry, ingest as ontology_ingest

ONTOLOGY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ontology_state.json")


def _atomic_write_json(path: str, obj) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

DEFAULT_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telemetry.json")
REDIS_KEY = "dexia:telemetry"


# --------------------------------------------------------------------------- #
# Sinks
# --------------------------------------------------------------------------- #
class JsonFileSink:
    """Writes each telemetry record to a JSON file via atomic replace."""

    def __init__(self, path: str = DEFAULT_JSON_PATH) -> None:
        self.path = path
        self.kind = "json"

    def publish(self, record: dict) -> None:
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f)
            os.replace(tmp, self.path)   # atomic on Windows + POSIX
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def close(self) -> None:
        pass


class RedisSink:
    """Publishes telemetry to Redis (Phase A event bus):
    * XADD to a capped Stream ``dexia:telemetry`` (persistent log / replay)
    * PUBLISH to pub/sub channel ``dexia:telemetry`` (low-latency push -> SSE)
    * SET the latest snapshot (so late subscribers get current state instantly)
    """

    def __init__(self, key: str = REDIS_KEY, maxlen: int = 2000) -> None:
        import redis  # only imported if available

        self.client = redis.Redis(host="127.0.0.1", port=6379, db=0, socket_connect_timeout=0.5)
        self.client.ping()              # raises if no server -> caller falls back
        self.key = key
        self.maxlen = maxlen
        self.kind = "redis"

    def publish(self, record: dict) -> None:
        payload = json.dumps(record)
        # approximate trimming (~) is much cheaper than exact
        self.client.xadd(self.key, {"data": payload}, maxlen=self.maxlen, approximate=True)
        self.client.publish(self.key, payload)
        self.client.set(self.key + ":latest", payload)

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass


class DualSink:
    """Writes to several sinks (JSON file for the polling fallback + Redis for
    the SSE push path), so both consumers keep working."""

    def __init__(self, sinks: list) -> None:
        self.sinks = sinks
        self.kind = "+".join(s.kind for s in sinks)

    def publish(self, record: dict) -> None:
        for s in self.sinks:
            try:
                s.publish(record)
            except Exception:
                pass

    def close(self) -> None:
        for s in self.sinks:
            s.close()


def make_sink(prefer_redis: bool = True, json_path: str = DEFAULT_JSON_PATH):
    """JSON file is always written (polling fallback); Redis is added when
    reachable (real-time SSE push). Returns a DualSink or a plain JSON sink."""
    json_sink = JsonFileSink(json_path)
    if prefer_redis:
        try:
            return DualSink([json_sink, RedisSink()])
        except Exception as exc:
            print(f"[telemetry] Redis unavailable ({type(exc).__name__}); "
                  f"JSON-file only -> {json_path}")
    return json_sink


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #
def _role(agent_id: str) -> str:
    if agent_id.startswith(RECON_PREFIX):
        idx = agent_id.rsplit("_", 1)[-1]
        return f"Recon {idx}"
    idx = agent_id.rsplit("_", 1)[-1]
    return f"Kamikaze {idx}"


def build_record(env: DroneMARLEnv, tick: int, infos: dict) -> dict:
    """Assemble a JSON-serializable telemetry record for this tick."""
    team = infos[env.possible_agents[0]]
    armed = bool(team.get("armed", True))
    agents = []
    wind_mags = []
    for aid in env.possible_agents:
        info = infos.get(aid, {})
        # Skip dormant pool agents (parked off-map) — only render live units.
        if not info.get("active", True):
            continue
        st = env.engines[aid].get_state()
        wind_mags.append(float(info.get("wind_mag", 0.0)))
        speed = float(np.linalg.norm(st.velocity))
        agents.append({
            "id": aid,
            "role": _role(aid),
            "kind": "recon" if aid.startswith(RECON_PREFIX) else "kami",
            "pos": [float(x) for x in st.position],
            "vel": [float(x) for x in st.velocity],
            "euler_deg": [float(np.rad2deg(a)) for a in st.orientation],
            "alt": float(st.position[2]),
            "speed": speed,
            "snr_db": float(info.get("snr_db", 0.0)),
            "link_good": bool(info.get("link_good", True)),
            "lost": bool(info.get("lost", False)),
            "loss_reason": info.get("loss_reason"),
            "active": bool(info.get("active", True)),
            "deployable": bool(info.get("deployable", False)),
            "state": "flying" if armed else "staged",
            "equipment": (env._spawn_meta.get(aid, {}) or {}).get("profile_name") or "Standard Quad",
        })

    aa_tel = env.aa.telemetry() if env.aa is not None else None
    aa = None
    if aa_tel is not None:
        aa_info = team.get("aa", {})
        tracked = list(aa_info.get("tracked", []))
        destroyed = list(aa_info.get("destroyed", []))
        fired = bool(aa_info.get("fired", False))
        # Dynamic threat level for AoE colouring: idle -> radar -> kill.
        if destroyed or fired:
            threat_level = "kill"      # target in kill zone
        elif tracked:
            threat_level = "radar"     # target in radar
        else:
            threat_level = "idle"
        aa = {
            "position": [float(x) for x in aa_tel["position"]],
            "radar_range": float(aa_tel["radar_range"]),
            "engagement_range": float(aa_tel["engagement_range"]),
            "ew_range": float(aa_tel["ew_range"]),
            "radar_half_angle_deg": float(aa_tel["radar_half_angle_deg"]),
            "radar_dir": [float(x) for x in aa_tel["radar_dir"]],
            "ammo": int(aa_tel["ammo"]),
            "max_ammo": int(aa_tel["max_ammo"]),
            "threat_level": threat_level,
            "active_zones": [
                {"center": [float(x) for x in c], "radius": float(r), "ttl": int(ttl)}
                for (c, r, ttl) in aa_tel["active_zones"]
            ],
            "tracked": tracked,
            "fired": fired,
            "engaged": aa_info.get("engaged"),
            "destroyed": destroyed,
        }

    return {
        "tick": tick,
        "time": datetime.now(timezone.utc).isoformat(),
        "agents": agents,
        "aa": aa,
        "target": [float(x) for x in env.target],
        "base_station": [float(x) for x in env.base_station],
        "loiter_center": [float(x) for x in env.loiter_center],
        "events": {
            "broadcast": bool(team.get("broadcast", False)),
            "kill_confirmed": bool(team.get("kill_confirmed", False)),
            "detection_event": float(team.get("detection_event", 0.0)),
            "kill_event": float(team.get("kill_event", 0.0)),
            "newly_lost": int(team.get("newly_lost", 0)),
            "total_lost": int(team.get("total_lost", 0)),
        },
        "network_survivability": float(team.get("network_survivability", 1.0)),
        "wind_gust": float(max(wind_mags)) if wind_mags else 0.0,
        "armed": armed,
        "enemy": [float(x) for x in env.target],         # 적 진지
        "friendly": [float(x) for x in env.base_station],  # 아군 진영
        "pool": {
            "available": sum(1 for a in env.deployable_ids if not env.is_active(a)),
            "total": len(env.deployable_ids),
            "deployed": sum(1 for a in env.deployable_ids if env.is_active(a)),
        },
    }


# --------------------------------------------------------------------------- #
# Scripted scenario
# --------------------------------------------------------------------------- #
def make_env(seed: int = 5) -> DroneMARLEnv:
    return DroneMARLEnv({
        "num_recon": 2, "num_kami": 4, "seed": seed,
        # Object pool of dormant deployable kamis for live C2 spawning (Phase 7).
        "pool_size": 10,
        # Mild wind ON for a live "wind gust" readout on the GCS. Wind is applied
        # as a COM force (no torque), so idle drones drift slightly but cannot
        # tip/crash -> the scripted AA scenario stays deterministic.
        "enable_wind": True, "enable_baro": False, "enable_sensor_noise": False,
        "wind_sigma": 0.7, "gust_prob": 0.05,
        "enable_aa": True,
        # AA defends the target from the ground; short range so the HIGH recon
        # observation point is safe but the LOW kami strike run is intercepted.
        "aa_config": {
            "position": [5.0, 5.0, 0.0],
            "radar_dir": [0.0, 0.0, 1.0],
            "radar_range": 3.0,
            "radar_half_angle_deg": 80.0,
            "fire_cooldown": 5,
            "kill_radius": 1.2,
            "zone_ttl": 4,
        },
    })


def scenario_positions(env: DroneMARLEnv, tick: int) -> dict:
    """Return scripted positions for the actively-maneuvering drones."""
    out = {}
    # recon_0 climbs to the observation point above the target over ticks 0..18
    recon0 = "agent_recon_0"
    op = env.observation_point
    start_r = np.array([0.0, 0.0, 1.0])
    fr = min(1.0, tick / 18.0)
    out[recon0] = start_r + (op - start_r) * fr

    # kami_0 holds in loiter until t=22, then runs in to the target by t=46
    kami0 = "agent_kami_0"
    if tick >= 22:
        fk = min(1.0, (tick - 22) / 24.0)
        start_k = env.loiter_center.copy()
        out[kami0] = start_k + (env.target - start_k) * fk
    return out


# --------------------------------------------------------------------------- #
# Interactive scenario-builder mode (C2 placement + ACTIVATE)
# --------------------------------------------------------------------------- #
def make_interactive_env(seed: int = 5) -> DroneMARLEnv:
    """Empty field: 0 pre-placed drones, a large pool to deploy from, one enemy
    AA, a friendly base — all relocatable from the HUD. Starts DISARMED."""
    return DroneMARLEnv({
        "num_recon": 0, "num_kami": 0, "pool_size": 12, "seed": seed,
        "armed": False,                       # placement phase until ACTIVATE
        "enable_wind": True, "enable_baro": False, "enable_sensor_noise": False,
        "wind_sigma": 0.5, "gust_prob": 0.03,
        "enable_aa": True,
        "base_station": [0.0, 0.0, 0.0], "loiter_center": [-3.0, -3.0, 1.5],
        "target": [5.0, 5.0, 1.0],
        "aa_config": {
            "position": [5.0, 5.0, 0.0], "radar_dir": [0.0, 0.0, 1.0],
            "radar_range": 3.5, "radar_half_angle_deg": 85.0,
            "fire_cooldown": 6, "kill_radius": 1.3, "zone_ttl": 4, "ammo": 30,
        },
    })


def takeoff_action(engine, target_alt: float = 1.6) -> np.ndarray:
    """Stable collective-thrust altitude hold: lifts a staged (grounded) drone to
    ``target_alt`` and holds it. Equal thrust on all motors -> no roll/pitch
    torque, so it stays upright (wind, a pure COM force, only translates it)."""
    st = engine.get_state()
    alt = float(st.position[2])
    vz = float(st.velocity[2])
    base = float(engine.hover_action[0])          # normalised hover level
    u = 0.7 * (target_alt - alt) - 0.30 * vz      # PD on altitude
    a = float(np.clip(base + u, -1.0, 1.0))
    return np.full(engine.N_MOTORS, a, dtype=np.float64)


def run(ticks: int | None, hz: float, json_path: str, prefer_redis: bool) -> int:
    sink = make_sink(prefer_redis=prefer_redis, json_path=json_path)
    env = make_interactive_env()
    obs, infos = env.reset(seed=5)

    # DroneOntology registry (Phase 6) — populated post-physics from telemetry.
    registry = InMemoryRegistry()

    dt = 1.0 / hz if hz > 0 else 0.0

    print(f"[telemetry] sink={sink.kind} | INTERACTIVE scenario-builder | "
          f"pool={len(env.deployable_ids)} | DISARMED (place units, then ACTIVATE) | "
          f"ontology=on | streaming{'' if ticks is None else f' {ticks} ticks'} ...")

    t = 0
    try:
        while ticks is None or t < ticks:
            # 0) drain & apply live C2 commands from the HUD (non-blocking).
            for cmd in drain_commands():
                res = env.apply_command(cmd)
                print(f"  tick {t:3d}: C2 {res.get('action')} "
                      f"{res.get('agent_id') or ''} ok={res.get('ok')}"
                      f"{'' if res.get('ok') else ' ('+str(res.get('reason'))+')'}")

            # 1) actions: ARMED -> physics takeoff/altitude-hold per active drone;
            #    DISARMED -> env freezes drones regardless, so any action is fine.
            if env.is_armed():
                actions = {
                    aid: takeoff_action(env.engines[aid])
                    for aid in env.possible_agents if env.is_active(aid)
                }
                for aid in env.possible_agents:
                    actions.setdefault(aid, env.engines[aid].hover_action)
            else:
                actions = {aid: env.engines[aid].hover_action for aid in env.possible_agents}

            obs, rew, term, trunc, infos = env.step(actions)
            record = build_record(env, t, infos)
            sink.publish(record)

            # --- Ontology (Phase 6): flat record -> typed object graph -----
            ontology_ingest(registry, record)
            _atomic_write_json(ONTOLOGY_PATH, registry.snapshot())

            if record["aa"] and record["aa"]["destroyed"]:
                print(f"  tick {t:3d}: AA-KILL {record['aa']['destroyed']} "
                      f"(total_lost={record['events']['total_lost']})")

            t += 1
            if dt:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\n[telemetry] interrupted by user.")
    finally:
        sink.close()

    print(f"[telemetry] done: streamed {t} ticks via {sink.kind}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Dexia telemetry streamer")
    ap.add_argument("--ticks", type=int, default=None, help="number of ticks (default: forever)")
    ap.add_argument("--hz", type=float, default=10.0, help="stream rate in Hz")
    ap.add_argument("--json", default=DEFAULT_JSON_PATH, help="telemetry JSON output path")
    ap.add_argument("--no-redis", action="store_true", help="force JSON file sink")
    args = ap.parse_args()
    return run(args.ticks, args.hz, args.json, prefer_redis=not args.no_redis)


if __name__ == "__main__":
    raise SystemExit(main())
