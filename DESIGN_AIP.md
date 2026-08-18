# Dexia — AIP Fusion & Wargame Expansion (Design Spec)

> Dexia를 "단일도메인 드론 자율비행 데모"에서 **"여러 소스를 융합한 전장 그림 위에서,
> AI가 오퍼레이터 의도에 맞춰 *이종 자산 중 옳은 것*을 골라 운용하는 AIP 슬라이스"**로
> 확장하기 위한 설계 기준 문서. Palantir AIP 오마주의 *하부 절반*(데이터 통합·온톨로지
> 폭·융합·거버넌스된 실장비 운용)을 채운다.
>
> 이 문서는 [ARCHITECTURE Dexia.md](ARCHITECTURE%20Dexia.md)(현재 구현)를 대체하지 않고
> 그 **불변 경계(`PhysicsEngine` ABC / `DroneState` / 사후 직렬화 / ActionBus funnel) 위에
> 얹는** 확장의 청사진이다.

---

## 0. 왜 — 무엇이 비어 있었나

Palantir = **Foundry(데이터 하부)** + **AIP(AI 상부)**. 현재 Dexia는 AIP 상부(온톨로지·
로직블록·LLM 게이트웨이·HITL·Evals)를 충실히 오마주했지만 다음이 비어 있었다:

1. **데이터 통합/온톨로지 폭** — 객체 타입이 사실상 드론 + AA 둘. 융합할 게 하나뿐.
2. **다중소스 융합** — 단일 시뮬 스트림 → AI가 "융합"이 아니라 한 줄기를 읽음.
3. **이종 자산 운용** — AI가 추천하는 게 드론 경로뿐. "적재적소 장비 선택"이 없음.
4. **관측 가능한 추론** — 단발 assess. "어떻게 수집·조합·판단했나"가 안 보임.
5. **실 장비 명령** — 거버넌스된 명령 경로는 있으나 드론에 국한.

이 문서의 확장이 1–5를 채운다.

---

## 1. 시스템이 만족해야 할 5개 조건 (수용 기준)

| # | 조건 | 검증 방법 |
|---|------|-----------|
| C1 | **시나리오 = 데이터** — 전구(중동/유럽/우크라/한반도)·임무가 데이터로 표현, 100개+ | `scenarios/*.yaml` 로드·검증, 라이브러리 카운트 |
| C2 | **장비 = 데이터** — 다양한 장비가 코드 아닌 카탈로그 데이터로 등장 | `equipment_catalog.yaml` 엔트리 추가만으로 신규 장비 동작 |
| C3 | **정보수집이 AI의 행동** — 부족하면 AI가 능동적으로 ISR 태스킹 | 추론 트레이스에 `task_isr` 선행 사이클 존재 |
| C4 | **추론이 관측 가능** — 수집→조합→로직→선택→명령 사슬이 보임 | `reasoning_trace.jsonl` + HUD 추론 타임라인 |
| C5 | **AI가 실 장비에 명령** — 판단이 곧 장비 운용으로 나감 | ActionBus→command→effect 해소로 표적 처리 관측 |

핵심 철학: **"딸칵 해결"이 아니다.** AI가 *어떤 정보를, 어떻게 조합해, 어떤 로직과
생각으로* 문제를 푸는지가 산출물의 일부다.

---

## 2. 통합 추상 — Equipment Catalog (다양한 장비 = 데이터)

모든 장비(아군·적군)는 `equipment_catalog.yaml`의 한 엔트리. **카탈로그 하나가 5개 서브시스템을
동시에 구동**한다 — 그래서 장비 추가가 코드가 아니라 데이터 한 줄이 된다:

```
equipment_catalog.yaml 엔트리
  ├─ sensors[]        → 피드 관측모델 (무엇을 어떤 정밀도로 보나)
  ├─ emits            → 융합 탐지가능성 (SIGINT이 잡을 수 있나)
  ├─ effects[]        → AssetMatch 가용성 + 시뮬 효과해소
  ├─ commands[]       → 이 장비가 받는 ActionType (LLM 툴로 자동 노출)
  └─ constraints      → 탄약/쿨다운/준비도
```

### 2.1 카탈로그 스키마

```yaml
<equipment_key>:
  side: blue | red
  domain: land | air | sea | space | cyber
  role: fires | isr | strike | ew | air_defense | maneuver | sensor
  sensors:                         # 이 장비가 제공하는 관측 (피드의 원천)
    - {type: eo_ir|radar|sigint|acoustic, range_m, fov_deg, pos_noise_m, detects: [armor,...]}
  effects:                         # 이 장비가 가하는 효과
    - {type: indirect_fire|direct_fire|loiter_strike|jam|recon_reveal,
       range_m, min_range_m, lethal_r, tof_s, degrades: comms|radar}
  emits: {type: radar_emitter, detectable_by: [sigint]}   # 적 장비가 내뿜는 신호(선택)
  commands: [request_fires, task_isr, jam, move, recall]   # 받는 ActionType
  constraints: {ammo: true, cooldown_s: 30, shoot_and_scoot: true}
```

`category`(armor/infantry/emplacement/emitter/air_defense/air)는 적 객체를 추상 분류하여
센서 `detects`/효과 적합성과 매칭하는 공통 어휘다.

---

## 3. 시나리오 포맷 (C1)

```yaml
scenario:
  id: ua-east-armor-thrust-007
  theater: eastern_europe         # middle_east | eastern_europe | korea | ...
  mission:
    tasking: "동측 진격 적 기갑대대 저지. 아군손실 최소, 부수피해 회피."  # 오퍼레이터 의도(자유문)
    intent: delay                 # deny | destroy | delay | recon | seize
    roe: [no_civilian_area]
    victory: {hold_line: [4200, 0], max_blue_loss: 2, time_limit_s: 600}
  blue:                           # 아군 전력 배치 (카탈로그 key 참조)
    - {cls: m777_howitzer, n: 2, pos: [-1000, 0]}
    - {cls: tb2_recon_uav, n: 1, pos: [-1500, 0]}
    - {cls: switchblade,   n: 4, pos: [-1200, 0]}
    - {cls: ew_jammer_gnd, n: 1, pos: [-900, 0]}
    - {cls: ugs_field,     n: 3, pos: [800, 0]}
  red:
    - {cls: t72_tank, n: 8, behavior: advance, route: [[6000,0],[3000,0]]}
    - {cls: sa11_sam, n: 1, pos: [5000, 1500], behavior: static_ad}
    - {cls: krasukha_ew, n: 1, pos: [5500, 0], behavior: periodic_jam}
  feeds: [sigint, ugs, uav_eo]    # 이 시나리오에서 가용한 정보 출처
```

**Validator** (`scenario.validate(catalog)`): 모든 `cls`가 카탈로그에 존재 · side 일치
(blue 전력은 blue 장비) · feeds 알려진 값 · victory 키 존재 · 도달가능성 사전점검.
시나리오를 *생성*(템플릿+LLM)할 때 품질 게이트로도 쓴다.

---

## 4. 데이터 흐름 (확장, 불변경계 위)

```
scenario.yaml + equipment_catalog.yaml
        │ load
   WorldState(지상진실: blue assets + red entities)   ← 물리 불필요, 스크립트 기동
        │ 각 피드가 catalog.sensors 관측모델 적용(노이즈/사각/누락)
   feeds ─► FusionEngine ─► TrackStore(conf · sources · uncertainty · 수명)
        │
   ┌──── AGENT LOOP (다단계 — 단발 assess 아님) ──────────────────────┐
   │  perceive → fuse → [AssetMatch + CommsRisk: 결정론]               │
   │           → TacticalAssess(LLM: intent + 카탈로그 가용툴)         │
   │           → 결정: 정보부족? task_isr/jam(수집) : 효과 자산 운용    │
   │           → ActionBus(거버넌스: clearance+MAC+감사) → command      │
   │           → WorldState 효과해소 → 결과 관측 → 루프                 │
   │  매 사이클 DecisionRecord ─► reasoning_trace.jsonl                 │
   └────────────────────────────────────────────────────────────────────┘
        │
   Evals: 시나리오별 성공/손실/결정품질 (라이브러리 = 평가 스위트)
        │
   HUD: 다전구 지도 + 융합 트랙(신뢰도/출처) + 추론 타임라인
```

---

## 5. 융합 설계 (C2 핵심)

### 5.1 급소 — 피드는 *불완전하고 서로 달라야* 한다
모든 피드가 지상진실을 완벽히 보면 융합은 no-op 중복제거다. 융합이 의미를 가지려면 각 피드가
자기만의 관측모델(커버리지·노이즈·누락·탐지대상)을 가져야 한다.

| 피드 | 커버리지 | 탐지 | 위치정밀 | 한계 |
|------|---------|------|---------|------|
| `uav_eo` | 드론 FOV 콘 + LOS | armor/emplacement | 높음(±5m) | 콘 밖 0, 격추 시 끊김 |
| `ugs` | 센서타워 반경 | 지상 armor | 중간(±20m) | emitter/공중 못 봄 |
| `sigint` | 전장 전역 | 활성 emitter | 방위/구역(±150m) | 활성일 때만 |

### 5.2 Track 모델
```python
Track{ track_id, category, position(추정), uncertainty_r, confidence[0..1],
       sources[], last_seen_tick, heading, status: active|coasting|stale }
```

### 5.3 FusionEngine 알고리즘
```
매 틱:
  detections = ∪ feed.observe(truth)
  for det: t = associate(det, tracks)   # category 일치 + 게이트반경(피드노이즈 스케일) 내 최근접
           if t is None: birth Track(det)
           else: t.update(det)           # 정밀도가중 추정, sources∪, last_seen=now
  for t: if now - t.last_seen > COAST: t.confidence *= DECAY
         if t.confidence < DROP: t.status = stale
```
- 단일소스 conf 상한 ~0.6, 다중소스 corroboration → ~0.9. → AI에 *시간을 아는 불확실한* 그림.
- 출처추적(`sources`)이 곧 객체단위 데이터 리니지 + AAR("언제 어느 센서로 첫 탐지").

### 5.4 레지스트리 결정 (**확정**)
적 트랙은 *기억*을 가져야 한다(이번 틱 미관측 시 coast). 현재 `registry.replace()`는 기억이 없음.
→ **레지스트리에 타입별 수명정책**을 둔다: 아군/미션 = replace(지상진실), Track = persist+merge.
별도 저장소로 갈리지 않게 하여 OAG/snapshot 경로를 단일 유지.

---

## 6. 자산 선택 + 실 장비 운용 (C3 · C5)

### 6.1 신규 ActionType (자동 툴 노출)
[dexia/ontology/actions.py](dexia/ontology/actions.py)에 등록만 하면 `ollama_tools()`가 LLM
메뉴에 자동 추가하고 ActionBus가 clearance+검증을 자동 적용한다:

| ActionType | 자산 | 검증자 | 효과 |
|-----------|------|--------|------|
| `request_fires` | artillery/MLRS | 표적 사거리내 + ammo>0 | indirect_fire |
| `task_isr` | recon UAV/sensor | 대상이 저신뢰 트랙 | recon_reveal (피드 커버리지↑) |
| `jam` | EW | 대상이 emitter + ew_range내 | jam (적 comms/radar 열화) |
| (기존) `deploy/recall/activate/standby/engage` | 드론 | 기존 MAC | 기존 |

### 6.2 AssetMatchBlock (결정론, LLM 앞단)
`RouteOptimBlock` 옆 Execute Function. **적 트랙 × 아군 자산** 가용성 매트릭스를 계산해
OAG에 주입 → LLM은 *적합성을 발명하지 않고* 우선순위·의도정렬만 한다.
```
[ASSET-MATCH] (intent: delay)
  ArmorTrack-3 (conf 0.9, 동측 2.1km):
     ✓ m777_1  사거리내 탄약12  점수0.85 → request_fires
     ✓ kami_2  도달가능 LOS    점수0.6  → engage(킬체인MAC 충족)
     ✗ m777_2  사거리초과(3.4>3.0)
  Emitter-1 (conf 0.5, ±150m):
     ✓ ew_1    ew_range내      점수0.7  → jam
     ⚠ tb2_1   task_isr로 표적확정 선행(conf<0.6)
```

### 6.3 효과 해소 (C5)
카탈로그 `effects.type`마다 해소기 하나: `indirect_fire`(비행시간 후 lethal_r 처리) ·
`loiter_strike` · `jam`(Gilbert-Elliott 통신열화) · `recon_reveal`(구역 피드 추가).
[dexia/sitl_bridge.py](dexia/sitl_bridge.py)는 *진짜 하드웨어*로 나가는 이음새로 유지.

---

## 7. 에이전트 루프 + 추론 트레이스 (C3 · C4)

현재 [pipeline.py](dexia/ai/pipeline.py)는 루프의 *한 스텝*. 확장은 **다단계 루프**:
부족하면 수집부터(정보수집=행동), 충분하면 운용, 결과 관측 후 재판단 — 목표달성/오퍼레이터
개입/사이클상한까지. 루프 컨트롤러가 정지조건·무한루프 방지·HITL 개입점을 관리.

### 7.1 DecisionRecord 스키마 (`reasoning_trace.jsonl`)
```json
{ "cycle": 1, "tick": 120, "intent": "delay",
  "perceive": {"feeds": ["sigint"], "new": ["bearing 074° emitter?"]},
  "fusion":   [{"id":"EMIT-1","cat":"emitter","conf":0.4,"unc_r":150,"src":["sigint"]}],
  "gaps":     ["EMIT-1 conf 0.4<0.6 — 위치 부정확, 타격 불가"],
  "asset_match": {"EMIT-1":[{"asset":"tb2_1","cmd":"task_isr","score":0.8}]},
  "llm_reason": "SIGINT만으로 SAM 추정. 타격 전 TB2 ISR로 확정 필요(수집 우선).",
  "decision":   [{"cmd":"task_isr","asset":"tb2_1","area":"074°/12km","why":"EMIT-1 확정"}],
  "governance": [{"cmd":"task_isr","status":"accepted"}] }
```
이 로그가 곧 C4의 산출물 — HUD가 타임라인으로 그린다.

### 7.2 예시 워크스루 (위 우크라 시나리오)
1. **수집:** SIGINT가 074°에 emitter(±150m, conf0.4) → AI: 위치불확실, 타격불가 →
   `task_isr(tb2_1)`.
2. **조합:** TB2 EO가 SA-11 포착(±5m), 2소스 corroborate → conf0.9. 동시 UGS가 T-72 8대
   진격 탐지 → AI: SAM 먼저 무력화 → `jam(ew_1, SA-11)` + `request_fires(m777, 기갑종대)`.
3. **재판단(BDA):** TB2 피해평가 → 전차 3대 잔존, hold_line 유지 → `engage(switchblade, 잔존)`.
4. **종료:** Evals — hold_line✓, blue_loss 0(≤2)✓ → 성공.

---

## 8. 정직한 난점 (알고 가는 위험)

1. **컨텍스트 스코핑 — 벡터검색이 돌아온다.** 장비 수십 종 + 트랙 다수 + 교리가 쌓이면 OAG가
   LLM 한도 초과. "이 임무·이 결정에 관련된" 자산·트랙·교리만 추리는 검색/스코핑 단계 필요.
   (정형 상태엔 벡터DB 불요였으나, 장비카탈로그·교리=비정형엔 검색 필요.)
2. **단발 → 에이전트 루프 전환이 진짜 일.** 루프 컨트롤러(정지·HITL·무한방지)는 신규 덩어리.
3. **100개는 생성한다.** 손 authoring은 씨앗만. 전구 템플릿 + 파라미터 무작위화 + LLM 작가 +
   유효성 검증기(victory 도달가능?)가 있어야 100개 품질 유지.

---

## 9. 확정된 설계 결정

| 결정 | 선택 | 이유 |
|------|------|------|
| 드론 표현 | **유지 + 비드론 자산만 `FriendlyAsset` 추가** | 드론 킬체인 MAC이 `DroneObject`에 묶임 — 흡수 시 회귀 위험 |
| 트랙 기억 | **레지스트리 타입별 수명정책**(별도 저장소 X) | OAG/snapshot 경로 단일 유지, 온톨로지로서 정직 |
| 지상진실 적 엔티티 | **스크립트 시나리오**(고정+단순기동) | 융합·자산선택 명제 증명에 물리 불필요 |
| 신규 액션 노출 | **레지스트리 등록만**(블록 코드 무수정) | 단일 funnel 철학 — 툴·검증·엔드포인트 드리프트 불가 |

---

## 10. 빌드 순서 (데이터 척추부터)

- [ ] **1. Equipment Catalog + Scenario 포맷** ← 모든 게 의존. (`dexia/scenario/`)
- [ ] 2. 카탈로그 구동 피드/센서 + FusionEngine + TrackStore (`dexia/fusion/`)
- [ ] 3. 카탈로그 구동 장비 효과해소 + 신규 ActionType
- [ ] 4. Agent Loop + AssetMatchBlock + Reasoning Trace
- [ ] 5. 시나리오 라이브러리 (씨앗 → 생성 → 100)
- [ ] 6. Evals 하니스 (라이브러리 = 평가 스위트)
- [ ] 7. HUD: 추론 타임라인 + 다전구 지도 + 트랙 신뢰도/출처

> 진행 상태는 세션 작업목록(Task #1–7)과 동기화. 각 단계는 듀얼모드 테스트로 검증.
