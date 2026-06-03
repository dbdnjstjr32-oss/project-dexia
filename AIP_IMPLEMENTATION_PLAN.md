# Dexia AIP 전환 — 구현 계획 (현황 매핑 + 잔여 로드맵)

> 입력: `Dexia_AIP_Architecture_Plan.docx` (Palantir AIP 전환, Phase 6–10)
> 이 문서: **계획서 Phase 6–10** ↔ **현재 실제 구현 상태**를 대조하고, 남은 작업을
> 의존성 순서로 재정렬한 실행 계획.

> ⚠️ **번호 주의**: 계획서의 "Phase 1–5 완료"는 *시뮬레이터* 단계(3-DOF→GCS HUD)다.
> 그 위에 우리가 최근 세션에서 만든 것들(Drone Garage, Object Pool, 시나리오 빌더,
> **Redis/SSE, FastAPI 제어 API, Ollama 에이전트**)은 계획서 Phase 6–10의 *일부를
> 순서를 건너뛰며* 이미 충족한다. 아래 표가 그 대조다.

---

## 0. 한 줄 현황
이벤트 버스(Redis/SSE)·제어 API(FastAPI)·실 LLM 에이전트(Ollama)는 **동작하는
수직 슬라이스로 이미 존재**한다. 다만 **온톨로지(시맨틱 레이어)·k-LLM 게이트웨이·
Logic 블록 체인·Evals·선언적 배포**가 빠져 있고, **AI 에이전트가 아직 프론트(HITL)와
온톨로지에 연결되지 않았다**(여전히 flat telemetry + 프론트 mockRag).

---

## 1. 계획서 Phase ↔ 현재 상태 매핑

| 계획서 Phase | 핵심 산출물 | 상태 | 근거 / 빠진 것 |
|---|---|---|---|
| **6. DroneOntology** | schema·registry·ActionBus·serializer | ❌ **미완** | flat `telemetry.json`만 존재. 온톨로지 객체/관계/MAC 검증 없음 |
| **7. Registry API + k-LLM** | OSS REST `/ontology/*`, SSE `/stream`, k-LLM Gateway | 🟡 **부분** | ✅ SSE `/api/stream`+Redis, ✅ FastAPI `dexia/api/sim_api.py`(쓰기 경로). ❌ `/ontology/*` 쿼리, ❌ 멀티모델 게이트웨이/감사 |
| **8. OAG + Logic 블록** | 실 LLM, TacticalAssess/KillChain/CommsRisk/RouteOptim 블록 | 🟡 **부분** | ✅ 실 Ollama 에이전트 `dexia/ai/tactical_agent.py`(함수호출 COA). ❌ OAG(온톨로지 주입), ❌ Logic 블록 체인, ❌ 프론트 mockRag 대체 |
| **9. Evals + 관찰가능성** | EpisodeEvalSuite, jsonl 감사 3종, Evals 패널 | ✅ **완료** | `dexia/evals/`(metrics·audit·suite), 6메트릭 임계값 자동판정, `evals_results.jsonl`, `/api/evals/*`, HUD `EvalsPanel`, `eval_phase9.py`/`test_phase9_evals.py` |
| **10. DexiaRuntime** | docker-compose, dexia.config.yaml, HealthMonitor, 에어갭 | 🟡 **부분** | ✅ Redis 컨테이너(`dexia-redis`) 주 경로화. ❌ compose 전체/설정파일/헬스모니터/에어갭 |

---

## 2. ✅ 완성된 기술 (재사용 가능 자산)

**시뮬레이션 코어 (계획서 "Phase 1–5")**
- `PhysicsEngine` ABC + 3-DOF NumPy / 6-DOF MuJoCo (`dexia/physics/`) — **불변 경계**
- MARL 환경 `DroneMARLEnv` (Ray MultiAgentEnv, 정적 에이전트 집합 + 손실 래치) — **불변 경계**
- Gilbert-Elliott 코밍, 바람 DR, Anti-Air, SITL/PWM 브리지

**최근 세션 추가분 (계획서 Phase 6–10에 직접 대응)**
| 자산 | 파일 | 계획서 대응 |
|---|---|---|
| **Redis 이벤트 버스** (Stream+PubSub+Latest, JSON 폴백 듀얼싱크) | `telemetry_stream.py` | Phase 10 "redis 주 경로", Phase 7 "/stream CDC" |
| **SSE 푸시 + 폴링 폴백** | `dexia-hud/pages/api/stream.js`, `lib/useTelemetry.js` | Phase 7 "SSE /stream", HUD 연동 변경 |
| **FastAPI 제어 평면** (Pydantic 검증, 명령 큐 적재) | `dexia/api/sim_api.py` | Phase 7 "POST /actions" (Funnel 쓰기 경로 일부) |
| **실 Ollama 전술 에이전트** (함수호출 COA, llama3.1/qwen2.5) | `dexia/ai/tactical_agent.py` | Phase 8 "실 LLM 전술 판단" 핵심 |
| **Object Pool 동적 배치** (RLlib 공간 불변 유지) | `drone_marl_env.py` | Phase 6 액션(deploy/abort)의 실행 기반 |
| **시나리오 빌더 + ACTIVATE 게이트** (적/아군/드론 배치, 물리 이륙) | HUD + env `armed` 게이트 | C2/HITL 실행 계층 |
| **명령 큐 + 웹훅** | `command_queue.py`, `integrations/webhook.py` | ActionBus 이벤트 발행 토대 |
| **Drone Garage** (커스텀 MJCF 프로파일) | `mujoco_builder.py`, `api/profiles.js` | (계획서엔 없는 보너스 — 온톨로지 DroneObject.profile로 흡수 가능) |

---

## 3. ⬜ 해야 할 것 (Gap)

1. **온톨로지 시맨틱 레이어 전무** — DroneObject/ThreatObject/KillChainLink, Registry, ActionBus(MAC 검증), serializer. (Phase 6)
2. **OSS 쿼리 API 없음** — FastAPI에 `/ontology/drones|killchain|threats|snapshot` 읽기 엔드포인트. (Phase 7)
3. **k-LLM 게이트웨이 없음** — 멀티모델(로컬 llama3 + 클라우드 Claude/GPT) 라우팅·감사로그. (Phase 7)
4. **OAG·Logic 블록 없음** — 에이전트가 flat 요약을 받음(온톨로지 주입 X), 단일 호출(블록 체인 X). (Phase 8)
5. **프론트 mockRag 미대체** — HUD 우측 패널이 아직 `mockRag.js` 규칙 사용. 실 에이전트와 미연결. (Phase 8)
6. **HITL 결재 루프 미완** — 에이전트 COA → 지휘관 [승인/거절] → API 실행 사이클의 프론트 카드가 없음.
7. **Evals 프레임워크·감사로그 3종 없음** — EpisodeEvalSuite, `action_audit.jsonl`/`llm_audit.jsonl`/`ontology_state.jsonl`, HUD Evals 패널. (Phase 9)
8. **선언적 배포 없음** — `docker-compose.yml`(5서비스), `dexia.config.yaml`, HealthMonitor, 에어갭 모드. (Phase 10)

---

## 4. 🎯 재정렬 구현 계획 (의존성 + 빠른 가치 순)

> 계획서는 Phase 6(온톨로지) 선행이지만, **이미 동작하는 에이전트+API+SSE가 있으므로**
> 가장 빠른 가치인 **HITL 루프 완성**을 Sprint 1로 당기고, 그 뒤 온톨로지를 *밑에* 깐다.

### Sprint 1 — HITL 결재 루프 완성 ⚡ (며칠, 최고 ROI)
*이미 만든 agent+API+SSE를 프론트에 연결만 하면 "감지→AI판단→인간승인→물리실행" 완결*
- `dexia/api/sim_api.py`에 `POST /api/sim/assess` 추가 → 텔레메트리로 `TacticalAgent.assess()` 호출 → COA 반환
- HUD 우측 패널에 **AI 참모 제안 카드** (평가 + 권고 + [승인]/[거절]) — `mockRag.js` 자리 대체
- [승인] 클릭 → `to_api_call()` 매핑대로 `/api/sim/*` POST → 실제 드론 전개
- ✔ 검증: 위협 시나리오 → 카드 표출 → 승인 → 드론 회수/배치 실행

### Sprint 2 — Phase 6 DroneOntology (시맨틱 레이어)
- `dexia/ontology/schema.py` (DroneObject·ThreatObject·MissionObject·KillChainLink·CommsLink)
- `serializer.py` (DroneState→OntologyObject) — **리스크 대응: 별도 스레드/사후 변환**, 시뮬 루프는 기존 dict 유지
- `registry.py` (InMemoryRegistry, upsert/query)
- `action_bus.py` (스키마 검증 + 감사로그; **학습 중 bypass 모드**)
- `drone_marl_env.py`에 `registry.upsert()` 훅(불변 경계 안 건드림), 스트리머가 `ontology_state.json` 병렬 기록
- ✔ 검증: `tests/test_ontology.py`

### Sprint 3 — Phase 7 완성 (OSS API + k-LLM Gateway)
- 기존 FastAPI에 `/ontology/drones|killchain|threats|snapshot` 추가(Registry 위임)
- `dexia/api/llm_gateway.py` — 멀티모델 라우터(로컬 llama3 / 클라우드 Claude·GPT) + `llm_audit.jsonl`
- TacticalAgent를 게이트웨이 경유로 전환(에어갭=로컬, 고정밀=클라우드)
- ✔ 검증: 엔드포인트 응답 + 감사로그 적재

### Sprint 4 — Phase 8 OAG + Logic 블록
- 에이전트 입력을 **온톨로지 스냅샷**으로 교체(OAG), flat 요약 폐기
- Logic 블록 체인: `TacticalAssessBlock`(LLM) → `KillChainDecisionBlock`(LLM, temp=0) → `CommsRiskBlock`(NumPy) → `RouteOptimBlock`(NumPy) → `MissionUpdateBlock`(ActionBus)
- 프론트 `mockRag.js` **완전 제거** → 실 LLM 블록 결과 표면화
- ✔ 검증: 블록별 단위 + 체인 통합

### Sprint 5 — Phase 9 Evals + 관찰가능성 ✅ 완료
- ✅ 감사로그 3종(`ontology_state`/`action_audit`/`llm_audit`) 불변 트레일 리더 — `dexia/evals/audit.py`(읽기 전용 요약)
- ✅ `EpisodeEvalSuite` 6메트릭 임계값 자동 판정 (Kill Efficiency·Recon Survival·Net Surv·AA Engagement·Broadcast Latency·LLM Accuracy) — `dexia/evals/{metrics,suite}.py`
- ✅ `evals_results.jsonl` 누적 트레일 + HUD `EvalsPanel`(`/api/evals/*` 프록시, 라이브 mission-so-far 평가)
- ✅ CLI `eval_phase9.py`(체크포인트 롤아웃) + `test_phase9_evals.py`(임계값/파서/누적/어댑터 전부 PASS)

### Sprint 6 — Phase 10 DexiaRuntime
- `docker-compose.yml` (dexia-sim 3.12 / dexia-api 3.13 / dexia-hud / dexia-evals / redis)
- `dexia.config.yaml` (scenario·hz·llm_provider·evals·aa 토글) + HealthMonitor(틱 정체 감지→재시작)
- 에어갭 모드(llama3 GGUF, MapLibre mbtiles 오프라인 타일)
- ✔ 검증: 단일 명령 기동 + 헬스체크

---

## 5. 즉시 착수 (다음 1개)
**Sprint 1 (HITL 결재 카드)** — 가장 적은 작업으로 AIP 루프가 *눈에 보이게* 완성된다.
이미 `TacticalAgent`(실 LLM)와 `sim_api`(실행 경로)와 SSE가 다 있으므로, **프론트 카드 +
`/assess` 엔드포인트 연결**만 하면 된다.

> 리스크 메모(계획서 §4 반영): ① 온톨로지 직렬화는 별도 스레드(시뮬 루프 무지연),
> ② LLM 레이턴시는 비동기+AbortController(HUD 렌더 분리), ③ ActionBus는 학습 중 bypass,
> ④ Python 3.12/3.13 분리·`PhysicsEngine` ABC·정적 에이전트 집합은 **불변**.
