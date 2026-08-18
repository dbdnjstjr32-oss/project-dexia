# Dexia — 근본·치명 문제 통합 감사 보고서

> 병렬 5개 팀 동시 정밀 감사 결과 통합본. 작성일 2026-06-21.
> 대상: `dexia/` 백엔드(160 소스 / 18 서브시스템) · `dexia-hud/` Next.js · 루트 스크립트/테스트 · SQLite + 파일 IPC.
> 제외: `.venv312/`, `site-packages/`, `node_modules/`, `.next/`, `__pycache__/`, 레거시 `Dexia_Wargame_Sim_Final/`.
>
> 팀별 상세: [team1 물리/킬체인](team1_physics_killchain.md) · [team2 상태/동시성](team2_state_concurrency.md) · [team3 AI/AIP/LLM](team3_ai_aip_llm.md) · [team4 서버/런타임/인증](team4_server_runtime_auth.md) · [team5 HUD/평가/증명](team5_hud_evals_proofs.md)

---

## 수정 진행 상황 (2026-06-21, 감사 후 착수)

> git 체크포인트: A `2b64fb4` · C-1 `5f29178` · B `9cc3d87` (베이스라인 `7da1931`). 각 수정은 실제 코드 재확인 후 적용, tests/ 95 passed·2 skipped 유지.

| 근본문제 | 항목 | 상태 | 비고 |
|---|---|---|---|
| A | A-1 하드코딩 키 | ✅ 수정 | `auth.py` fail-closed (env/config 키만, dev는 `DEXIA_ALLOW_DEFAULT_KEYS` 명시 opt-in). config.yaml 유효 키 제거. 검증: 기본값에서 `dexia-commander` 거부 |
| A | A-2/A-6 무통제 폴백 | ✅ 수정 | `command.js` 폴백 제거(503 반환), `commandStore.js` 쓰기 경로 삭제 → Node writer 소멸 |
| A | A-3 command_server | ✅ 수정 | CORS 제한, WS Origin/`DEXIA_WS_TOKEN` 검사, 127.0.0.1 바인딩 |
| A | A-4 stale ontology | ✅ 수정 | `sim_api`가 agent_id 가드(recall)를 stale 스냅샷에서 fail-closed |
| A | A-5 락 degradation | ⚠️ 완화 | 경고 추가(관측화). 동시성 회귀(763==763) 보존 위해 제어흐름 유지. A-6은 A-2로 근본해소 |
| C | C-1 공중 승리불가 | ✅ 수정 | `_is_detection` 표적-비종속 수평 footprint+최소고도. 신규 테스트 3개로 고도 무관 탐지 증명 |
| C | C-2 죽은 km-scale 킬체인 | ⛔ 보류 | 사용자 결정(킬체인 배선 보류) |
| C | C-3/C-4 미사일 물리 | ⛔ 보류 | km-scale physics3d (C-2와 동일 보류 영역) |
| B | B-1 가짜 증명 | ✅ 수정 | `run_e2e_proof` fire_source 추적·LLM 결정점 부여·exit0은 LLM COA 필수. 오프라인서 정직 거부(exit 1) |
| B | B-2 가짜 게이트 | ✅ 수정 | `available()`가 서버+모델 도달성 실측. 검증: 미가동 시 False |
| B | B-3 무상가점/블루손실 | ⚠️ 부분 | 블루 손실 미모델링을 정직 문서화; B-4로 false-pass 제거됨. 완전수정(블루 attrition)은 킬체인과 함께 보류 |
| B | B-4 타임아웃=성공 | ✅ 수정 | `score_mission` in_progress 페널티 → pass율 인플레 ~80%→정직 40% |

HIGH군(H1–H6 등)은 대부분 위 근본수정에 종속. 미처리 HIGH(예: 미사일 dt/좌표계, 서킷브레이커 싱글톤, geo-anchor, verify_workflow 포트)는 보류 영역이거나 별도 후속. 세부는 각 팀 문서 참조.

---

## 한 줄 결론

**프로젝트의 핵심 가치 명제 — "통제된(governed) · 정직한(honest) · 물리 기반(physics-grounded) AI 킬체인" — 을 떠받치는 세 기둥이 각각 독립적으로, 그리고 서로 맞물려서 구조적으로 비어 있다.** 5개 팀이 서로 다른 코드 영역에서 출발했는데도 모두 같은 세 가지 근본 결함으로 수렴했다. 이는 표면 버그가 아니라 설계 차원의 결함이며, 개별 라인 수정으로는 닫히지 않는다.

---

## 교차 검증된 3대 근본 문제 (Root Causes)

### 근본문제 A — "단일 통제 라이트-퍼널"은 허구다 (인증 우회 + 우회 경로 + 낡은 상태 검증)

시스템의 안전성 주장 전체가 "모든 상태 변경 행위는 하나의 통제된 쓰기 퍼널을 통과하며, ontology 상태에 대해 권한·계보(lineage)가 검증된다"에 걸려 있다. 이 funnel은 **세 방향에서 동시에 뚫려 있다.**

- **추측 가능한 기본 키 = 권한 우회.** `dexia/api/auth.py:30-33`이 `dexia-commander`/`dexia-operator`를 소스에 박아두고, 운영 `dexia.config.yaml:56-59`이 **동일 리터럴 키**를 재사용하며, HUD `dexia-hud/pages/api/command.js:21`이 commander 키를 하드코딩한다. `X-Dexia-Key: dexia-commander` 헤더 하나로 누구나 지휘관 권한 획득. *(team4-C1)*
- **퍼널을 통째로 우회하는 무통제 경로.** FastAPI가 느리거나 닿지 않으면 Next.js 라우트 `command.js:71-90`가 조용히 폴백해 raw 명령을 `commands.json`에 직접 쓰고, 이를 `telemetry_stream.py:445` → `drone_marl_env.py:856 apply_command`이 **권한·ActionBus·계보 검증 없이** 실행한다. AIP 설계가 의존하는 audit/lineage 추적이 우회 가능하며 불완전하다. *(team4-C2, team2-C2/C3)*
- **낡고 동기화되지 않은 상태에 대해 검증한다 (TOCTOU).** ActionBus 가드가 검증에 쓰는 `ontology_state.json`은 **다른 프로세스**가 틱당 1회 쓰는 파일인데, 공유 락도 시퀀스 번호도 신선도(freshness) 검사도 없이 bare `open()/json.load()`로 읽는다. 스트리머는 `os.replace` 8회 재시도 실패 시 조용히 쓰기를 건너뛸 수 있고, ontology 신선도를 감시하는 코드는 없다. → "LOST 드론은 교전 불가", "broadcast 전 kamikaze 교전 불가" 같은 가드가 **임의로 오래된 상태**로 판정되어, 막아야 할 것을 승인하고 승인해야 할 것을 막을 수 있는데 그 사실이 어디에도 신호되지 않는다. *(team2-C1/H3, sim_api.py:135-173, action_bus.py:98-111)*
- **두 번째 통제면도 무방비.** command_server(포트 8001)는 인증 0, `CORS *` + credentials, 단일 전역 가변 세션 → 어느 오리진이든 미션을 START/RESET/MODIFY 가능하고 동시 운용자가 서로의 상태를 덮어쓴다. *(team4-C3)*

> **종합:** 거버넌스/감사 funnel은 (1) 사실상 접근 통제가 없고(키 하나), (2) 통째로 우회 가능하며, (3) 통과하더라도 낡은 상태로 잘못 판정한다. 세 개 모두 닫지 않으면 funnel은 의미가 없다.

### 근본문제 B — "정직한 AIP 킬체인" 증명이 결정 주체를 위조한다

프로젝트 최우선 명제인 "honest verification"이 검증하는 대상이 잘못되어 있다. LLM 자체는 진짜다(team3 확인: 실제 Ollama 추론, 가변 0.4–64s 지연, 실제 토큰 수와 500 에러). **그러나 그 LLM이 킬체인을 닫는다는 것은 증명되지 않는다.**

- **킬은 하드코딩된 지휘관 명령에서 나온다.** `run_e2e_proof.py:153-159,178-188`의 "CHAIN PROVEN"은 스크립트된 `command_fires()`(LLM 0)로 통과하고, 검증 로직은 어떤 LLM 출력도 검사하지 않는다. 캡처된 실제 런에서 모든 LLM COA는 **거부**되었고 킬은 `CMD-001`(지휘관 지시)이었다. **모델이 꺼져 있어도 동일하게 통과한다.** *(team3-C1)*
- **"라이브 증명" 게이트가 모델 도달성을 확인하지 않는다.** `llm_gateway.py:63-64`의 `available()`는 `ollama` 패키지 import 여부만 본다 → `run_e2e_proof.py:96-98`의 정직성 게이트가 **모델 로드 0인 상태에서도** 통과. *(team3-C2)*
- **AI 추론 산출물이라며 결정론적/목(mock) 데이터를 제시한다.** `reasoning_trace.jsonl`/`scenario_evals.jsonl`은 `model`/`llm` 필드 없는 `policy.decide` 결정론 출력 *(team3-H1)*; AAR 독트린 루프는 24/25 에피소드를 `MockLLMClient` 캔드 응답으로 돌렸고 `OllamaLLMClient`는 예외 시 조용히 mock 폴백 *(team3-H2)*; `episodic_memory.jsonl`은 24/25가 `"llm":"mock"`.
- **채점이 통과 쪽으로 조작되어 있다.** 엔진의 어떤 코드도 Blue를 죽이지 않으므로(`effects.py` 리졸버는 `red.alive=False`만, `sam_can_engage`/`engage_air`는 미연결) `blue_lost`가 **구조적으로 항상 0** → 모든 미션이 0.3 무상 가점, `fail_blue_loss` 도달 불가 *(team5-C1)*. 16-사이클 캡에서 타임아웃된 미션(`in_progress`)도 부분 성공으로 채점되어, `scenario_evals.jsonl` 20행 중 16행이 `in_progress`인데 0.69–0.95로 대부분 0.7 PASS 통과 *(team5-C2)*.

> **종합:** "정직한 증명"은 정직하게 *물리/BDA*를 계산하지만, *결정의 주체*를 AIP로 귀속시키는 부분이 위조다. AI를 떼어내도 증명은 통과하고 점수는 더 잘 나온다.

### 근본문제 C — 물리가 양립 불가능한 두 세계로 분열, 게임이 구조적으로 승리 불가

두 개의 물리적으로 호환되지 않는 세계가 한 저장소에 스테이플로 박혀 있다. 라이브 게임 `DroneMARLEnv`은 장난감 **미터 스케일**(타깃 기준 단일 탐지/킬 구체)에서 돌고, tier-B `physics3d`/SAM 스택은 실제 **km / 1500 m-AGL 스케일**로 작성됐으나 **테스트에서만 도달**된다.

- **"공중 고도가 게임을 승리 불가로 만든다" 버그의 직접 원인.** `drone_marl_env.py:326-328`(`_is_detection`)은 타깃 `[5,5,1]` 기준 3D 구체 `dist ≤ 4.0` **AND** `alt ≥ 2.5`를 동시에 요구한다 — 수치적으로 alt ≥ 5 m면 구체 내부로 들어갈 수평 도달거리가 0이 되어 탐지가 기하학적으로 불가능. 킬 게이트(`:599`)는 같은 z=1 타깃에 0.7 m 구체. **고도를 올릴수록 탐지/broadcast/킬이 전부 죽는다.** *(team1-C1)*
- **진짜 공중 킬체인은 죽은 코드(dead code).** `fusion/effects.py:238-265`(`engage_air`/`sam_can_engage`)과 카탈로그 SAM(28–75 km)은 어떤 러너에도 연결되지 않는다(코드 주석이 자인). 게임이 실제로 스텝하는 유일한 방공은 `radar_range=8.0 m`/`kill_radius=1.5 m`의 `AntiAirBattery`로, 1500 m-AGL 공중 표적에 닿을 수 없다. *(team1-C2)* 엔진 레벨에서도 동일 증상: AD/SAM 표적이 킬 목표에서 제외(jam만 가능)되고, 지상 fire는 지형 높이를 조준해 공중 표적은 원천 불가 *(team5-M7/M8)*.

> **종합:** "air altitude blocks winnable game"은 단일 버그가 아니라 이 분열의 직접적이고 검증된 증상이다. `los_min_altitude`/`detection_radius` 튜닝으로는, 게이트가 z=1 타깃 기준 단일 구체로 남아 있는 한 고쳐지지 않는다.

### 세 근본문제가 맞물리는 방식

```
물리 공중 킬이 죽어 있음 (C)
        └─▶ 유일한 킬은 지휘관 스크립트 (B의 C1)
                  └─▶ 그 명령을 검증하는 funnel이 낡고(stale) 우회 가능(A)
                            └─▶ 채점은 Blue 손실 0·타임아웃을 성공으로 보상 (B의 C1/C2)
```
"승리 가능 · 정직 · 통제됨" 3요소가 세 축에서 **동시에** 입증되지 않는다. 한 축만 고치면 나머지 둘이 그대로 구멍으로 남는다.

---

## CRITICAL 통합 목록 (14건)

| # | 영역 | 파일:라인 | 문제 | 근본문제 |
|---|------|-----------|------|:---:|
| A-1 | 인증 | `api/auth.py:30-33` · `dexia.config.yaml:56-59` · `dexia-hud/pages/api/command.js:21` | 하드코딩된 기본 키 재사용 → 헤더 1개로 지휘관 권한 우회 | A |
| A-2 | 거버넌스 우회 | `dexia-hud/pages/api/command.js:71-90` · `telemetry_stream.py:445` · `drone_marl_env.py:856` | FastAPI 폴백이 raw 명령을 무통제로 `commands.json`에 기록·실행 | A |
| A-3 | 명령서버 | `agent/command_server.py:20-26,49,80-169` | 포트 8001 인증 0 · CORS * + credentials · 단일 전역 세션 | A |
| A-4 | TOCTOU | `api/sim_api.py:135-173` · `ontology/action_bus.py:98-111` · `telemetry_stream.py:478-480` | 가드가 낡고 비동기화된 `ontology_state.json`으로 판정 | A |
| A-5 | 락 누락 | `integrations/command_queue.py:116-117,138-139,217-242` | `commands.json` 락이 best-effort, 실패 시 무락 read-modify-write → lost update | A |
| A-6 | 락 미공유 | `integrations/command_queue.py:31-39,237-242` | Node 작성자가 Python 락을 공유 안 함 → 동시 기록·중복 처리 | A |
| B-1 | 가짜 증명 | `run_e2e_proof.py:153-159,178-188` | 킬이 스크립트 `command_fires`(LLM 0)에서 발생, 검증이 LLM 출력 미검사 → 모델 꺼도 PASS | B |
| B-2 | 가짜 게이트 | `api/llm_gateway.py:63-64` → `run_e2e_proof.py:96-98` | `available()`가 import만 확인, 모델 도달성 미확인 | B |
| B-3 | 채점 무상가점 | `fusion/effects.py:96-141` · `red_commander.py` · `loop.py:173` | 엔진이 Blue를 죽이지 않음 → `blue_lost` 항상 0 → 0.3 무상, `fail_blue_loss` 도달 불가 | B |
| B-4 | 채점 위조 | `agent/campaign.py:31-47` · `loop.py:157-160` | 타임아웃(`in_progress`) 미션을 부분 성공으로 채점 (16/20행 PASS) | B |
| C-1 | 공중 승리불가 | `envs/drone_marl_env.py:326-328,599` | 고도 게이트 + 고정반경 구체가 alt↑에서 수평 도달 0 → 탐지/킬 불가 | C |
| C-2 | 죽은 킬체인 | `fusion/effects.py:238-265` | 진짜 SAM/공중 교전 코드가 어떤 러너에도 미연결(dead code) | C |
| C-3 | 미사일 비물리 | `physics3d/missile.py:33-42` | PN 횡가속 무제한 → 순간 선회, 그럼에도 4845 m 미스 → `hit` 판정 무효 | C |
| C-4 | 교전 판정 오류 | `physics3d/missile.py:44-60` | 최근접 break가 속도/dt 무관 고정 50 m slop → miss distance 신뢰불가 | C |

## HIGH 통합 목록 (24건)

**물리/킬체인 (team1)**
- H — 쿼터니언 규약 불일치: `physics3d/state.py:18-38`(ZYX) vs `physics/mujoco_engine.py:188`(`xyz`) 같은 `Body6` 공유 → 자세 발산
- H — 뱅크 부호 모순: `physics3d/air.py:127`(`roll=-bank`) vs `jsbsim_engine.py:115` 반대 부호(상호교환 선언에도)
- H — ENU/NED·compass↔course 혼용: `jsbsim_engine.py:76-105,139-151` JSBSim compass yaw를 ENU-course 빌더에 투입
- H — 엔진 dt 불일치/공유 타임베이스 없음: `fusion/world.py:146-151`(0.05) vs `missile.py:45`(0.05/t_max30) vs `ballistic.py:57`(0.25)
- H — `AntiAirBattery` dt 미전달: `anti_air.py:99-100,157`(기본 0.02) vs `drone_marl_env.py:235-244` 미주입, fire_cooldown은 정수 스텝 → 드리프트
- H — Gilbert-Elliott rate/확률 재해석: `comms/gilbert_elliott.py:96-101,159-163` 레거시 per-step 0.40을 rate로 → 50× 과소 스위칭, survivability 과대

**상태/동시성 (team2)**
- H — 감사 분기 + 무음 드롭: `ontology/action_bus.py:78-95` SQLite/JSONL 비원자적, DB 에러 삼킴 → 감사에서 행위 소실하는데 명령은 enqueue
- H — SQLite 교차 프로세스 쓰기 경합: `ontology/store.py:27,35-38,213-223` `_WRITE_LOCK`이 프로세스 내부용, `_STORE` 싱글톤 init 비동기화
- H — `ontology_state.json` 프레임 스킵 + 리더 신선도 가드 없음: `telemetry_stream.py:64-94` · `sim_api.py:296-308` (HealthMonitor가 엉뚱한 파일 감시)
- H — FusionEngine `self.tracks` 무한 증가 + 재시작 시 `_next` id 충돌: `fusion/engine.py:166-198,218-220`

**AI/AIP/LLM (team3)**
- H — `reasoning_trace`/`scenario_evals`가 결정론 `policy.decide`인데 AI 추론으로 제시: `loop.py:163-167` · `campaign.py:27`
- H — AAR 독트린 루프 24/25 `MockLLMClient`, 예외 시 무음 mock 폴백: `aip/logic_blocks.py:89-120,140-155`
- H — LLM 실패 → COA 없이 무음 진행, 재시도/하드스톱 없음: `agent/mission_manager.py:244-247,262-265`
- H — 서킷 브레이커가 싱글톤 전역 가변 상태 → 한 세션 장애가 전 세션·전 런 오염: `llm_gateway.py:23-24,170-177`

**서버/런타임 (team4)**
- H — WS 태스크/커넥션 누수: `command_server.py:39-44,122-125`
- H — 이벤트 루프에서 블로킹 LLM 호출(25s) → 2 Hz 브로드캐스트 동결: `command_server.py:140-146`
- H — 전역 config 1회 캐시·재로드 없음, `cfg.apply()` 부작용으로 임계값 변형: `runtime/config.py:148-157`
- H — Docker/의존성 드리프트: `docker/Dockerfile.evals:8-15`(numpy 누락 → crash-loop), 전 의존성 비고정 하한
- H — Windows 하드코딩 경로: `run_gcs_simulation.py:16`(`C:/Users/dbdnj/.gemini/...`), `..` 상대 cwd 의존
- H — SITL UDP 소켓 fd 누수: `sitl_bridge.py:128-153,203-216`(timeout 없는 lazy connect, close 미호출)

**HUD/평가 (team5)**
- H — `verify_workflow.py:10`이 `ws://localhost:8000` 연결하나 command server는 **8001** → 자체 E2E 워크플로 증명이 연결 불가
- H — `dexia-hud/lib/geo.js:28-43` · `wargame.js:87` `THEATER_ANCHORS`가 korea/eastern_europe만 → 나머지 전역이 Donbas 폴백, 엉뚱한 나라에 렌더
- H — `evals/suite.py:41-45` · `audit.py:49-88` 단일 틱 평가에 전역·교차런 `*_audit.jsonl` 누적을 접어 넣어 이번 에피소드 지표로 오표기
- H — `dexia-hud/pages/index.js:391` 소모 게이지가 `/6` 하드코딩, 실제 풀 12·초기 0 → 분모 오류

---

## 증거/증명물 신뢰도 판정 (honest-verification 명제 대상)

- **LLM 자체:** 진짜 (실측 지연/토큰/에러). *(team3)*
- **e2e 킬체인 증명:** **부분적 위조.** 물리/BDA는 ground-truth지만 킬 결정 주체가 LLM이 아니라 하드코딩 명령. 모델 오프라인에서도 PASS. *(team3-C1/C2)*
- **대시보드/verify 스크립트:** **대체로 실제 계산이나 stale.** Plotly HTML/`evals_results.jsonl`(1554행)/`scenario_evals.jsonl`은 실제 산출물. 단, PNG 대시보드는 16일 전 무버전 스냅샷이며 `operations.png`/`operations_active.png`는 동일 이미지를 before/after로 중복 제시. *(team5)*
- **채점/평가 지표:** **신뢰 불가.** Blue 손실 구조적 0·타임아웃 성공 채점·전역 누적 혼입. *(team5-C1/C2/H5)*

---

## 권장 조치 우선순위 (수정은 아직 안 함 — 착수 순서 제안)

1. **근본문제 C 먼저 결정:** 라이브 게임을 어느 스케일(미터 토이 vs km tier-B)로 통일할지 단일 결정. 이게 안 정해지면 A·B 수정도 헛돈다. 우선 `drone_marl_env.py`의 고도 게이트 + 단일 구체 탐지/킬을 표적-비종속 기하로 교체(C-1), 죽은 SAM 경로(C-2)를 연결하거나 명시적으로 제거.
2. **근본문제 A — funnel 봉인:** 기본 키 제거·환경변수화(A-1), `commands.json` 무통제 폴백 경로 제거(A-2), command_server 인증/CORS(A-3), 단일 락+신선도 시퀀스로 ontology 검증 동기화(A-4/A-5/A-6).
3. **근본문제 B — 증명 정직화:** 증명이 LLM COA가 실제 킬을 닫았음을 *검증*하도록 어서션 추가(B-1), 모델 도달성 실측(B-2), 채점에서 무상가점/타임아웃-성공 제거(B-3/B-4).
4. HIGH군은 각 근본문제에 종속 — 위 3개 봉인 과정에서 상당수 동반 해소.

---

*세부 근거·재현 수치·blast radius는 팀별 문서 5종 참조.*
