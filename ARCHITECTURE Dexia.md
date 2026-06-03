# Dexia — Architecture & Design

> **Dexia Drone Wargame Simulator** — a modular, defense-grade MARL (Multi-Agent
> Reinforcement Learning) drone wargame environment, built in 5 phases from
> 3-DOF kinematics up to a swarm kill-chain with a live Ground Control Station.
>
> 이 문서는 현재 구현된 전체 아키텍처, 데이터 흐름, 핵심 설계 결정을 한곳에 정리한다.

---

## 1. 시스템 개요

```
┌──────────────────────────── PYTHON CORE (시뮬레이션 + 학습) ────────────────────────────┐
│                                                                                        │
│   PhysicsEngine (ABC)        Comms / DR / Wargame            Envs (Gymnasium / RLlib)   │
│   ├─ Kinematic3DOF (NumPy)   ├─ Gilbert-Elliott (RSSI/SNR)   ├─ DroneWargameEnv (3DOF) │
│   └─ MuJoCoQuad (6-DOF)      ├─ WindField (OU + gusts)       ├─ DroneFlightSchoolEnv    │
│                              └─ AntiAirBattery (radar/kill)  └─ DroneMARLEnv (swarm)    │
│                                                                                        │
│   Training (Ray RLlib PPO) ──► checkpoints/   Viz (Plotly) ──► *.html                  │
└────────────────────────────────────────────────────────────────────────────────────────┘
                │  full_state per tick                         │ outbound events
                ▼                                              ▼
        telemetry_stream.py ──► telemetry.json        dexia/integrations/webhook.py
                │  (atomic write / Redis fallback)             │  (non-blocking POST)
                ▼                                              ▼
┌──────────── dexia-hud (Next.js GCS) ────────────┐   ┌──── Tactical Globe (외부 프로젝트) ────┐
│  /api/telemetry ──poll──► MapLibre GL 전술 지도  │   │  /api/internal/webhook ──► webhook_events│
│  + Swarm Telemetry / AI Staff (mock RAG) 패널    │   │  (SQLite WAL, 수신 전용)                  │
└─────────────────────────────────────────────────┘   └──────────────────────────────────────────┘
```

- **언어/런타임**: Python 3.13 (기본 엔진) + **Python 3.12 venv** (Ray) + Next.js 14 (HUD)
- **핵심 원칙**: `PhysicsEngine` ABC를 경계로 물리 엔진 교체 가능, 컴포넌트(comms/wind/AA) 주입식 → MARL 확장 용이

---

## 2. ⚠️ 런타임 분리: Python 3.13 + Python 3.12 venv

이 프로젝트에서 가장 중요한 환경 설정. **Ray는 Python 3.13 휠을 제공하지 않는다.**

| 용도 | 인터프리터 | 이유 |
|------|-----------|------|
| 기본 물리 엔진(MuJoCo 6-DOF), Phase 1 시뮬, 스모크 테스트 | **Python 3.13** (시스템) | MuJoCo 3.9는 `cp313` 휠 제공, Ray 불필요 |
| Ray RLlib 학습(PPO, MultiAgentEnv), Phase 2 / 2.5 / 3 | **Python 3.12 venv** (`.venv312/`) | Ray 2.55는 `cp312`까지만 휠 제공 |

`dexia/` 패키지 자체는 두 인터프리터에서 모두 import 가능. **학습 스크립트(`train_phase*.py`)만** venv 필요.

확정 버전(`.venv312/`): `ray 2.55.1 · gymnasium 1.2 · mujoco 3.9.0 · numpy 2.2.6 · torch 2.12.0+cpu`

---

## 3. 디렉터리 구조

```
Project Dexia/
├── ARCHITECTURE.md            ← (이 문서)
├── README.md                  사용법 + venv 설정
├── requirements.txt
│
├── dexia/                     ── 코어 파이썬 패키지 (인터프리터 무관)
│   ├── physics/
│   │   ├── base.py            PhysicsEngine(ABC), DroneState(3DOF), DroneState6DOF
│   │   ├── kinematics_3dof.py Kinematic3DOFEngine (NumPy 점질량 적분기, Phase 1)
│   │   └── mujoco_engine.py   MuJoCoQuadEngine (6-DOF 쿼드, 공식 mujoco 바인딩, Phase 2)
│   ├── comms/
│   │   └── gilbert_elliott.py GilbertElliottChannel (GOOD/BAD 마르코프 → RSSI/SNR/패킷손실)
│   ├── domain_randomization/
│   │   └── wind.py            WindField (OU 상시풍 + 라이즈드코사인 돌풍)
│   ├── wargame/
│   │   └── anti_air.py        AntiAirBattery (레이더 콘 + 위협 구역 + 격추), ThreatZone
│   ├── envs/
│   │   ├── drone_env.py       DroneWargameEnv (단일 3-DOF, Gymnasium, Phase 1)
│   │   ├── drone_env_6dof.py  DroneFlightSchoolEnv (단일 6-DOF + 커리큘럼, Phase 2)
│   │   └── drone_marl_env.py  DroneMARLEnv (Ray MultiAgentEnv 스웜, Phase 2.5 / 3 / 4)
│   ├── viz/
│   │   └── plotter.py         Plotly 멀티패널 에피소드 대시보드
│   ├── sitl_bridge.py         RL action[-1,1] ↔ PWM[1000,2000], MAVLink/UDP 목 (Phase 4)
│   └── integrations/
│       └── webhook.py         아웃바운드 내부 웹훅 클라이언트 (논블로킹, Sender)
│
├── train_phase2.py            6-DOF PPO 커리큘럼 학습
├── train_phase2_5.py          멀티에이전트 킬체인 (2 정책)
├── train_phase3.py            6기 스웜 학습 (전 스레드)
├── eval_phase3.py             체크포인트 롤아웃 → phase3_results.html
├── test_phase1.py             3-DOF 시뮬 + Plotly → phase1_results.html
├── test_phase4.py             AA 위협 + SITL 브리지 검증
├── test_phase5_backend.py     텔레메트리 스트리머 50틱 검증
│
├── telemetry_stream.py        DroneMARLEnv → telemetry.json (시나리오 스트리머)
├── package_project.py         배포용 ZIP 패키징
├── checkpoints/               RLlib 체크포인트 (phase2 / phase2_5 / phase3)
│
└── dexia-hud/                 ── Next.js Ground Control Station (GCS)
    ├── pages/index.js         대시보드: 지도 + 좌/우 패널
    ├── pages/api/telemetry.js telemetry.json 서빙 API
    ├── components/TacticalMap.js  MapLibre GL (위성/하이브리드/전술 전환, 명령형 마커)
    ├── lib/geo.js             로컬 미터 ↔ WGS84 lon/lat 투영
    ├── lib/useTelemetry.js    폴링 훅 (렌더와 분리)
    └── lib/mockRag.js         AI 참모(모의 LLM RAG) 권고 규칙
```

---

## 4. 물리 계층 — `PhysicsEngine` ABC

전 시스템의 핵심 이음새. 환경은 **오직 이 인터페이스로만** 물리와 통신한다.

```python
class PhysicsEngine(ABC):
    dt: float
    def reset(position, velocity=None) -> DroneState
    def step(action, external_force=None) -> DroneState   # external_force = 바람 등 외란
    def get_state() -> DroneState
```

| 구현 | 자유도 | 액션 | 비고 |
|------|--------|------|------|
| `Kinematic3DOFEngine` | 3-DOF (x,y,z 병진) | 축별 가속도 정규화[-1,1] | 순수 NumPy, 반암시적 오일러, GPU 불필요 |
| `MuJoCoQuadEngine` | **6-DOF** (병진+자세) | 모터 4개 PWM/추력[-1,1] | 공식 `mujoco` 바인딩, X-쿼드 MJCF, 추력+요 반작용 토크 |

- `DroneState` = 위치(3)+속도(3). `DroneState6DOF`는 이를 상속해 오일러각(3)+각속도(3)+쿼터니언(4) 추가 → 모든 곳에서 호환.
- **6-DOF 잠금 해제**가 새 엔진 추가만으로 가능했던 이유 = 이 ABC 경계 덕분.

---

## 5. 환경 진화 (Gymnasium → Ray MultiAgentEnv)

| 환경 | Phase | 에이전트 | 관측/액션 | 핵심 |
|------|-------|----------|-----------|------|
| `DroneWargameEnv` | 1 | 단일 | 11-D obs / 3-D act | 3-DOF + GE 코밍 + 바람, 웨이포인트 |
| `DroneFlightSchoolEnv` | 2 | 단일 | 16-D obs / 4-D act | 6-DOF, **커리큘럼**(HOVER→WAYPOINT), 자세 안정 보상 |
| `DroneMARLEnv` | 2.5/3/4 | **N 정찰 + M 자폭** | 에이전트별 16-D / 4-D | Ray `MultiAgentEnv`, 정책별 분리, 킬체인 |

### `DroneMARLEnv` (현재 주력) 설계
- **에이전트당 컴포넌트 1세트**: MuJoCo 엔진 + GE 채널 + WindField → MARL 확장 자연스러움.
- **이종 정책 매핑**: `agent_recon_*` → `policy_recon`, `agent_kami_*` → `policy_kami` (역할 접두사 기반).
- **킬체인 + 관측 마스킹**: 정찰기가 표적을 탐지(레이더 반경+LOS)하면 `broadcast` 래치 → 그 전까지 자폭기 관측의 표적 좌표는 **0 마스킹**.
- **정적 에이전트 집합**: 격추된 드론도 관측은 계속 반환(`_lost` 래치) → RLlib 계약상 가장 견고.
- **손실 사유 래치**(`_loss_reason`): `crash` / `anti_air`를 영구 보존 → HUD/AI참모가 사유를 계속 표시(전이 버그 방지).

---

## 6. 보상 — 복합 팀 보상 (Phase 3)

```
R_team   = w1·Detection + w2·Kill_Confirmed + w3·Network_Survivability − w4·Total_Loss
R_recon  = R_team − β·(Exposure_Time + Detection_Risk)        (+ dense shaping)
R_kami   = R_team − ζ·(Comms_Quality_Drop + Path_Risk)        (+ dense shaping)
```

| 가중치 | 값 | 의미 |
|--------|-----|------|
| w1 / w2 / w3 / w4 | 10 / 100 / 2 / 50 | 탐지 / 격추 / 네트워크 생존 / 손실 |
| β / ζ | 0.5 / 0.5 | 정찰 노출·피탐 / 자폭 통신·경로 위험 |
| w_shape | 1.0 | dense 항법 셰이핑(희소 보상 학습 가능화) |

- 자폭기: 방송 전 로이터존 이탈 시 **대형 페널티**, 방송 후 표적 타격 시 **대형 보상**(w2).
- 자세 안정: roll²+pitch² + 각속도 + 제어 저크 페널티 → 부드러운 비행 유도.

---

## 7. 도메인 랜덤화 (Phase 3, "Extreme DR")

| 스트레서 | 구현 | 비고 |
|----------|------|------|
| **바람** | `WindField` OU 상시풍 + 라이즈드코사인 돌풍 | COM 힘(토크 없음) → 병진 외란, 전복 안 됨 |
| **기압/공력** | 고도 기반 추력 저하 + 무작위 압력 강하 | `engine.max_thrust` 동적 스케일 |
| **센서 노이즈** | 6-DOF 관측에 가우시안 주입 | 위치/속도/오일러/각속도별 std |

`enable_wind / enable_baro / enable_sensor_noise` 플래그로 게이팅 (Phase 2는 이상조건=OFF, Phase 3=ON).

---

## 8. 통신 모델 — Gilbert-Elliott

2상태 마르코프 체인(GOOD ⇄ BAD)으로 버스트성 패킷 손실 모델링:

```
RSSI = Tx − 경로손실(거리) − 페이딩(상태) + 열잡음    [dBm]
SNR  = RSSI − 잡음바닥                                [dB]
packet_lost ~ Bernoulli(PER[상태])
```

- 에이전트별 채널 1개(베이스 스테이션 링크). `Network_Survivability` = 양호 링크 비율.

---

## 9. 워게임 위협 — Anti-Air (Phase 4)

`AntiAirBattery` (순수 NumPy, 환경 비결합):
- **레이더 콘**: 위치 + 축 방향 + 반각 + 사거리 → 콘 내 드론 추적.
- **사격 주기**: 추적 + 쿨다운 해제 시 최근접 표적에 **위협 구역**(`ThreatZone` 구) 방출.
- **치명성**: 활성 구역과 교차하는 드론 즉시 격추 → 환경의 `Total_Loss`(w4)로 직결.
- `enable_aa` 플래그로 게이팅(기본 OFF, Phase 3 동작 불변 유지).

---

## 10. SITL 브리지 (Phase 4) — `dexia/sitl_bridge.py`

학습 정책(RLModule)과 실 비행 컨트롤러(PX4/ArduPilot) 사이 번역기. PX4 툴체인 불필요.

```
action[-1,1]  ──action_to_pwm──►  PWM[1000,2000] µs   (−1→1000, 0→1500, +1→2000)
```

- `FlightControllerLink`(ABC) + `MockUDPLink`(PX4 기본 UDP 전송, dry-run 안전).
- `to_mavlink_rc_override` (MAVLink `RC_CHANNELS_OVERRIDE` 스타일 포맷, 의존성 0).
- `SITLBridge`: obs → 정책 액션 → PWM → 링크.

---

## 11. 시각화 & 텔레메트리

### Plotly (오프라인 분석)
- `dexia/viz/plotter.py` — 3D 궤적 + 코밍 상태/RSSI + 패킷손실/바람 멀티패널 → `phase1_results.html`, `phase3_results.html`.

### 텔레메트리 스트림 (실시간)
- `telemetry_stream.py` — `DroneMARLEnv`(AA+바람) 루프 → `telemetry.json` 매 틱 **원자적 기록**(임시파일 replace), Redis 가용 시 발행 / 미가용 시 JSON 폴백.
- 시나리오: 정찰기 상승→방송, 자폭기 진입→AA 격추 (결정론적 데모).
- 레코드: 에이전트 6-DOF/속도/SNR/손실, AA 상태, 이벤트(broadcast/kill), 네트워크 생존율, 돌풍.

---

## 12. Ground Control Station — `dexia-hud/` (Next.js)

```
DroneMARLEnv ──► telemetry_stream.py ──► telemetry.json
                                            │ poll 200ms (useTelemetry)
                                    pages/index.js (Dashboard)
              ┌─────────────────────────────┼──────────────────────────┐
       TacticalMap (MapLibre GL)      LeftPanel (memo)          RightPanel (memo)
       위성/하이브리드/전술 전환        SWARM TELEMETRY           AI STAFF(mockRag)
       명령형 마커/위협반경 갱신        TARGET ACQUISITION        + 시스템 게이지
```

**렌더 최적화 (고FPS 목표):**
- 지도는 **1회만 생성**(마운트 effect) → React가 WebGL 캔버스를 재조정하지 않음.
- 텔레메트리 갱신은 **명령형**(`Marker.setLngLat`, `GeoJSONSource.setData`) → GPU 합성.
- `TacticalMap` + 패널 모두 `React.memo`, 폴링은 렌더와 분리.
- 베이스맵 전환은 레이어 **가시성 토글**(`setLayoutProperty`)만 → 스타일 리로드/마커 손실 없음.

**좌표 처리**: 시뮬은 로컬 미터 프레임(~15m). `lib/geo.js`가 `GEO_ANCHOR` 기준 lon/lat 투영 + `WORLD_SCALE` 표시 확대. 실 VTOL GCS 전환 시 `WORLD_SCALE=1` + 앵커=실 발사 좌표.

---

## 13. 외부 연동 — 내부 웹훅 (이벤트 드리븐)

Dexia(**Sender**) → Tactical Globe(**Receiver**) 경량 마이크로서비스 연결.

```
Dexia 이벤트 ──► dexia/integrations/webhook.py ──POST(x-internal-secret)──►
                  (논블로킹 데몬 스레드풀, urllib)
        Tactical Globe: app/api/internal/webhook/route.ts ──► webhook_events (SQLite WAL)
```

- **Sender**: `send_event(event_type, payload)` — 데몬 스레드풀 제출 후 즉시 반환(~0.6ms), 모든 실패(오프라인/타임아웃) 흡수 → Dexia 무중단.
- **Receiver**: POST 전용, `x-internal-secret` **타이밍 세이프** 검증, 시크릿 미설정 시 **fail-closed(503)**, 전용 `webhook_events` 테이블에만 삽입(기존 테이블 불변).
- 설정: 양측에 `INTERNAL_WEBHOOK_SECRET` 공유, Dexia에 `TACTICAL_GLOBE_WEBHOOK_URL`.

---

## 14. AIP 엔터프라이즈 계층 (Phase 6–10 — Palantir AIP 전환)

Phase 1–5의 시뮬 코어를 **온톨로지 중심의 Enterprise AI 플랫폼**으로 승격한다.
파일 I/O → 이벤트 버스, 규칙기반 mockRag → 실 LLM 에이전트, 하드코딩 루프 → FastAPI
마이크로서비스로 전환. **불변 경계(`PhysicsEngine` ABC / `DroneState` / 정적 에이전트
집합)는 건드리지 않고**, 시맨틱/AI 계층을 그 *위에* 얹는다.

### 14.0 전체 데이터 흐름
```
DroneMARLEnv (물리 — 불변)
   │ build_record (틱당 dict)
telemetry_stream.py  ──DualSink──┬─► telemetry.json (폴링 폴백)
   │                              └─► Redis: XADD/PUBLISH dexia:telemetry  ──► /api/stream (SSE) ──► HUD
   │ ontology_ingest (사후 변환)
   └─► ontology_state.json (타입 객체 그래프)
                    │
   FastAPI :8000 ──┤  OSS:  /ontology/drones·threats·killchain·snapshot
   (dexia-api)      └─ /api/sim/assess  ──► TacticalPipeline (OAG + Logic 블록)
                                              CommsRisk(NumPy) → TacticalAssess(LLM)
                                              → RouteOptim(NumPy) → KillChainDecision(LLM)
                                              → MissionUpdate(ActionBus 거버넌스)
                                                   │ COA(평가+권고+경로+거버넌스)
   k-LLM Gateway ──► Ollama(llama3.1/qwen2.5)      ▼
   (라우팅+llm_audit)                      HUD AIStaffCard [승인]/[거절] (HITL)
                                                   │ 승인 시
                                          /api/command ──► commands.json ──► 스트리머 ──► 물리 실행
```

### 14.1 이벤트 파이프라인 (Phase A) — Redis + SSE
- **`RedisSink`** (`telemetry_stream.py`): `XADD`(영속 스트림, MAXLEN 2000) + `PUBLISH`(저지연) + `SET :latest`(신규 접속 즉시 상태). **`DualSink`** 로 JSON 파일과 Redis를 동시 기록 → 폴링/SSE 양립.
- **SSE** (`dexia-hud/pages/api/stream.js`): node-redis 구독 → 브라우저 푸시. `useTelemetry` 훅이 **SSE 우선 + 폴링 자동 폴백** (Redis 죽어도 무중단).
- 인프라: `docker run --name dexia-redis -p 6379:6379 redis:7-alpine`.

### 14.2 시맨틱 레이어 — DroneOntology (Phase 6, Foundry/OMS)
| 모듈 | Palantir 대응 | 역할 |
|------|---------------|------|
| `dexia/ontology/schema.py` | **OMS** | DroneObject·ThreatObject·MissionObject + KillChainLink·CommsLink, ACTION_TYPES |
| `dexia/ontology/registry.py` | **OSS(저장)** | InMemoryRegistry (upsert/replace/query/snapshot, 스레드세이프) |
| `dexia/ontology/serializer.py` | — | telemetry → 타입 객체 (물리 루프 밖 사후 변환), `registry_from_snapshot` |
| `dexia/ontology/action_bus.py` | **Funnel** | 스키마+상태+MAC 검증 + `action_audit.jsonl`, **학습 중 bypass 모드** |

MAC 예: `_lost` 드론 move/engage 거부, kami는 broadcast 전 engage 불가(킬체인).

### 14.3 OSS API + k-LLM Gateway (Phase 7)
- **OSS REST** (`dexia/api/sim_api.py`): `GET /ontology/{drones,drones/{id},threats,killchain,mission,snapshot}` — `ontology_state.json`을 읽어 타입화 쿼리 서빙 (HUD/AI가 flat 폴링 대신 사용).
- **k-LLM Gateway** (`dexia/api/llm_gateway.py`): use_case 라우터(tactical→llama3.1 / summary→qwen2.5 / airgap→로컬), 멀티프로바이더(키 있으면 Claude/GPT 투명 전환), 모든 호출 `llm_audit.jsonl`(모델·토큰·레이턴시).

### 14.4 OAG + Logic 블록 (Phase 8) — mockRag 대체
- **OAG** (`dexia/ai/oag.py`): 온톨로지 스냅샷 → 3계층(미션/드론 객체그래프/위협+링크) 타입 컨텍스트를 LLM에 주입 (벡터DB 불필요 — 이미 구조화됨).
- **Logic 블록** (`dexia/ai/blocks.py`, `pipeline.py`): `CommsRiskBlock`·`RouteOptimBlock`(Execute Function/NumPy, 결정론) + `TacticalAssessBlock`·`KillChainDecisionBlock`(Use LLM, **temp=0**) + `MissionUpdateBlock`(Apply Action/ActionBus). 온도 전략: 전술 0 / 브리핑 0.3.

### 14.5 FastAPI 제어 평면 + HITL 결재 (Sprint 1)
- `POST /api/sim/{deploy,enemy,friendly,activate,standby,recall,clear}` (Pydantic 검증) → 명령 큐 적재.
- `POST /api/sim/assess` → OAG 파이프라인 → COA 반환.
- **HUD `AIStaffCard`**: `⚡ AI 전술 분석` → COA(평가·통신위험·우회경로·거버넌스) 표출 → 지휘관 **[승인]** 시에만 `/api/command`로 실제 물리 실행. *모델은 절대 자동 실행 안 함.*

### 14.6 관찰가능성 — 불변 감사 트레일 3종 (Phase 9 토대)
| 로그 | 계층 | 내용 |
|------|------|------|
| `ontology_state.json` | 시뮬 | 틱당 타입 객체 그래프 |
| `action_audit.jsonl` | 액션 | ActionBus 모든 쓰기 시도(승인/거부+사유) |
| `llm_audit.jsonl` | AI | k-LLM 호출(모델·토큰·레이턴시) |

---

## 15. 빌드 단계 요약

| Phase | 제목 | 산출물 | 상태 |
|-------|------|--------|------|
| 1 | Foundations | 3-DOF 시뮬, GE 코밍, 바람, Plotly | ✅ |
| 2 | Flight School | 6-DOF MuJoCo + PPO 커리큘럼 | ✅ |
| 2.5 | Micro-Team | `MultiAgentEnv`, 정찰+자폭 킬체인 (2 정책) | ✅ |
| 3 | Swarm + DR | 6기(2정찰+4자폭), 익스트림 DR | ✅ |
| 4 | Ground Threats + SITL | Anti-Air + SITL/PWM 브리지 | ✅ |
| 5 | GCS HUD | Next.js MapLibre 전술 지도(3D 지형·MGRS·심볼로지) | ✅ |
| 6 (AIP) | DroneOntology | schema/registry/serializer + ActionBus(MAC·감사) | ✅ |
| 7 (AIP) | OSS API + k-LLM | `/ontology/*` + Gateway(라우팅·감사) | ✅ |
| 8 (AIP) | OAG + Logic 블록 | OAG 컨텍스트 + 블록 체인, mockRag 제거 | ✅ |
| A/HITL | 이벤트 버스 + 결재 | Redis/SSE + FastAPI 제어 + 승인 카드 | ✅ |
| 9 | Evals + 관찰성 | EpisodeEvalSuite + Evals 패널 | ⬜ |
| 10 | DexiaRuntime | docker-compose + dexia.config.yaml + 에어갭 | ⬜ |

---

## 16. 실행

```powershell
# Phase 1 (3-DOF + Plotly) — 시스템 Python 3.13
python test_phase1.py

# 학습 (Phase 2~3) — 반드시 3.12 venv
.\.venv312\Scripts\python.exe train_phase3.py
.\.venv312\Scripts\python.exe eval_phase3.py

# --- 풀 AIP 스택 (터미널/서비스) ---
docker start dexia-redis                                          # 0) 이벤트 버스
.\.venv312\Scripts\python.exe telemetry_stream.py --hz 10         # 1) 스트리머(+온톨로지)
.\.venv312\Scripts\python.exe -m uvicorn dexia.api.sim_api:app --port 8000  # 2) 제어/AI API
cd dexia-hud; npm install; npm run dev                            # 3) HUD → localhost:3000
# (Ollama 데몬 :11434 가동 필요 — 로컬 LLM 추론)
```

> 검증 테스트: `test_aip_bc.py`(FastAPI+에이전트) · `test_ontology.py`(Phase 6) ·
> `test_phase7_oss.py`(OSS+게이트웨이) · `test_phase8_oag.py`(OAG+Logic 블록).

---

## 17. 핵심 설계 결정 (요약)

| 결정 | 이유 |
|------|------|
| `PhysicsEngine` ABC 경계 | 3-DOF↔6-DOF, NumPy↔MuJoCo 무중단 교체 + MARL 확장 |
| 컴포넌트 주입(엔진/코밍/바람/AA) | 에이전트당 1세트 → 스웜 자연 확장 |
| Python 3.12 venv 분리 | Ray의 3.13 미지원 회피, 엔진은 3.13 유지 |
| 정적 에이전트 집합 + 손실 래치 | RLlib MultiAgentEnv 견고성, HUD 사유 표시 일관성 |
| 명령형 지도 갱신 + memo | 고FPS, WebGL 캔버스 재조정 회피 |
| 웹훅 논블로킹 + fail-closed | Sender 무중단 / Receiver 보안 기본값 |
| **온톨로지 사후 변환**(스트리머) | 물리 hot loop 무지연, 불변 경계 보존 |
| **DualSink**(JSON+Redis) | Redis 장애 시 폴링 폴백 — 무중단 |
| **k-LLM Gateway 단일 인터페이스** | 멀티모델 라우팅 + 토큰/레이턴시 감사 + 에어갭 |
| **OAG(벡터DB 미사용)** | 상태가 이미 구조화 → 타입 객체+엣지 직접 주입 |
| **ActionBus 거버넌스 + HITL** | LLM 자동실행 금지, MAC 검증 → 인간 승인 → 실행 |
```
