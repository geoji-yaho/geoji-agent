# 🛠️ [Tech Spec] 기술 명세서: 작업 3 — 평결·템플릿 수직 흐름(우리 몫) · 스텁 핸들러 · 백엔드 클라이언트 · 벤더 클라이언트 · luna 실측 (M2 9/10)

> 근거: proposal2 §3 #10(최종 저장 주체 = 백엔드 finalize), §5.1(심문관 스텁), §5.4(OpenAI 실측 계획), §9.2 내부 API, §10 finalize 트랜잭션·결과 코드, §11.1 watchdog, §14.3 비용, §15 실패 처리(finalize 응답 유실), §20 작업 3 + 먼저 실패시킬 케이스(마지막 표 동시 도착 · 평결 commit 직후 프로세스 중단 · finalize 응답 유실 후 재전송).
> **선행 문서: 01·02.** 백엔드가 만드는 절반(begin-generation·finalize·generation-failed·commit record·watchdog·job INSERT·submissions)은 `10-backend-contract.md` 에 있다. 이 문서는 **그 API 를 호출하는 우리 쪽**과 M2 에 필요한 스텁·실측만 다룬다.
> M2 완료 기준(proposal2 §20): **AI 호출 없이** 등록 → 투표 → 템플릿이 노출되고 형량이 한 번만 확정된다. 백엔드가 계약대로 이벤트를 쏜다.

## 1. 개요 및 구현 목표

### 목적:
- 큐에서 `SENTENCE` 를 집어 `begin-generation` → `generation-failed(AI_NOT_READY)` 로 **즉시 템플릿 폴백을 유도**하는 스텁 핸들러 — M2 데모가 10초 watchdog 을 기다리지 않게 한다. 형량 1회 확정·commit record·watchdog 은 백엔드 몫이고, 우리는 그 API 를 **정확히 한 번의 결과**로 다루는 클라이언트 규약을 잠근다(동일 finalize 재전송 = 같은 결과, 응답 유실 시 모델 재호출 금지)
- `POST /internal/v1/intake` 스텁(항상 `PASS`) + 서비스 인증
- 두 벤더 키·클라이언트 설정과 **최소 어댑터** `openai_compat_llm.py` — 작업 6 이 예산·원장·오류 분류를 얹기 전에 실측에 필요한 만큼만
- **OpenAI `gpt-5.6-luna` 실측**(심문관·양형관·검수관, n=5). 양형관 p90 이 2초를 넘기면 D-23(유죄율 밴드별 사전 후보) 검토
- 먼저 실패시킬 것 중 우리 몫: 평결 commit 직후 워커 프로세스 중단 → 재기동 후 같은 job 을 새 generation 으로 처리 / finalize 응답 유실 → 같은 요청 재전송이 같은 200

### 핵심 플로우 (M2):
```
백엔드: 평결 커밋 + SENTENCE job INSERT ──▶ ai-worker claim ──▶ POST begin-generation(job_id, generation_id, verdict_version)
   ──▶ [M2 스텁] POST generation-failed(error_code=AI_NOT_READY)
   ──▶ 백엔드: fallback_sentence 로 FINAL(sentence_source=RULE) + 템플릿(text_status=TEMPLATE_READY) + RETAIN job. AI_NOT_READY 는 TEXT_RETRY 를 예약하지 않는다
   ──▶ complete(job)                         ──▶ 프론트 폴링: text_status=TEMPLATE_READY, view=템플릿
백엔드 watchdog(250ms): 10초 넘긴 PENDING 도 같은 폴백 — 워커가 죽어도 템플릿은 뜬다
POST /post-submissions ──▶ POST /internal/v1/intake (스텁: PASS, intake_source=FALLBACK) ──▶ 등록 + PREPARE job (스텁: no-op 성공)
```
- `generation-failed` 의 `error_code` 표는 proposal2 에 없어서 이 문서가 제안하고 10 §4.6 에 옮겼다: **재시도 없음** `AI_NOT_READY`·`POLICY_ERROR`·`EVIDENCE_INVALIDATED`, **round 예약** `VENDOR_UNAVAILABLE`·`BUDGET_EXCEEDED`·`EVAL_FAILED`·`SCHEMA_INVALID`·`DEADLINE_EXCEEDED` (`00-INDEX.md` §8.3)

### 현상태
| 항목 | 값 |
|---|---|
| 실측 | 서기(Grok) 만. OpenAI 4역할 **미실측** — 이 작업에서 3역할(조서는 작업 5 fixture 로 대체) |
| 백엔드 | 내부 API 5개 없음. 10 §12 시점 표에 따라 9/10 까지 begin-generation·finalize·generation-failed·watchdog·job INSERT 2지점이 필요 |

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | `adapters/backend_http.py` — 내부 API 5개 클라이언트, 서비스 인증, trace/request id, 타임아웃, 멱등 재전송 규약 | Critical | M |
| 3.2 | M2 스텁 핸들러 4종(SENTENCE·TEXT_RETRY → `generation-failed(AI_NOT_READY)`, PREPARE·RETAIN → no-op 성공) | Critical | S |
| 3.3 | `api/intake_routes.py` 스텁(`PASS`) + `api/auth.py`(`SERVICE_AUTH_TOKEN`) | High | S |
| 3.4 | `adapters/openai_compat_llm.py` 최소 구현 + 두 벤더 설정 검증 | High | S |
| 3.5 | luna 실측 — `scripts/probe_writer_latency.py --provider openai --role …` 확장, n=5 × 3역할 | High | S |
| 3.6 | 테스트용 가짜 백엔드 `tests/fakes/backend_app.py`(5 API 최소 상태 기계) + M2 통합 확인 | High | M |

### Out-of-Scope
- `begin-generation`·`finalize`·`generation-failed`·commit record·watchdog·job INSERT·submissions·폴링 API 구현 — 백엔드(10)
- 그래프(작업 5), 예산·원장·오류 분류·`node_results`(작업 6), intake 실구현(작업 7)
- 템플릿 **문구 자체**는 01 의 `contracts/fixtures/templates-v1.json`. 백엔드 watchdog 이 같은 파일을 쓴다(10 §10)

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 기한 |
|---|---|---|
| 백엔드 | 10 §4 의 `begin-generation`·`generation-failed`(오류 코드 표 포함), §5 finalize(스텁 단계에서는 호출되지 않지만 계약 고정), §6 watchdog, §3 job INSERT 2지점(`post.created`·`verdict.confirmed`) + finalize/watchdog 에서 `RETAIN` INSERT | **9/10** |
| 백엔드 | `POST /post-submissions` → `POST /internal/v1/intake`(`SERVICE_AUTH_TOKEN`, 타임아웃 5초) → `PASS` 면 `NEW → COMPLETED` | 9/10 |
| 백엔드 | `GET /posts/{id}/verdict?room_id=` 폴링 응답을 `verdict-view-v1` 로. 생성 중은 200 + `view=null` + `poll_after_ms` | 9/10 |
| 프론트 | 폴링 1초 → 15초 뒤 5초, 화면 이탈 시 취소, `text_version` 이 작은 응답으로 UI 를 덮지 않기, `view=null` 이면 대기 메시지 | 9/10 |

### 팀 결정 대기
- D-23 — 양형관 실측 p90 > 2초일 때: 양형관을 등록 시점으로 되돌리면 확정 평결을 모른 채 양형한다. 대안 = **유죄율 밴드별(50~69/70~89/90~100) 사전 후보 3개**를 PREPARE 에서 만들고 SENTENCE 에서 실제 밴드 것을 고른다(+2.4원, 등록 시점 +3초). 실측 뒤 결정
- 없음 — 키·결제는 9/8 확정(AI 파트 개인 계정, 팀 정산. 10 §15.3)

## 3. 기술 상세 설계 (Technical Design)

### 3.1 신규·변경 파일
| 파일 | 구분 | 내용 |
|---|---|---|
| `src/geoji_ai/adapters/backend_http.py` | 신규 | §3.2 `BackendPort` 구현(httpx.AsyncClient) |
| `src/geoji_ai/application/{sentence_case,prepare_case,retain_memory}.py` | 신규(스텁) | §3.3. 작업 4·5 가 본체로 교체 |
| `src/geoji_ai/api/intake_routes.py`, `api/auth.py` | 신규 | §3.4 |
| `src/geoji_ai/adapters/openai_compat_llm.py` | 신규(최소) | §3.5 |
| `scripts/probe_writer_latency.py` | 변경 | `--provider openai --role intake|sentencing|evaluator` + 역할별 초안 프롬프트·스키마(01 `llm_schemas`) 사용 |
| `tests/fakes/backend_app.py` | 신규 | §3.6 가짜 백엔드 |
| `tests/unit/test_backend_http.py`, `tests/integration/test_m2_vertical.py`, `tests/integration/test_worker_restart.py` | 신규 | §4.2 |

### 3.2 백엔드 클라이언트 (`adapters/backend_http.py`, 10 §4 계약)
| 항목 | 값 |
|---|---|
| 인증·헤더 | `Authorization: Bearer <SERVICE_AUTH_TOKEN>`, `X-Trace-Id`(job.trace_id), `X-Request-Id`(호출마다 uuid), `X-Job-Id`, `X-Generation-Id`. 로그에 토큰 금지 |
| 타임아웃 | snapshot 2s · resolve-evidence 2s · begin-generation 1s · finalize 3s · generation-failed 1s(connect 0.5s 공통). **DB 트랜잭션 안에서 부르지 않는다** |
| 재전송 | 전부 멱등이라 transport 오류·timeout 시 **같은 본문**을 최대 2회 재전송(백오프 200ms·600ms). finalize 재전송은 commit record 로 같은 200 을 받는다(10 §5). 재전송 실패면 job `fail(BACKEND_UNAVAILABLE, retry_after=5)` |
| 오류 매핑 | `409 STALE_GENERATION` → 결과 폐기, job complete(우리 세대가 아님) · `409 EVIDENCE_INVALIDATED` → 초안 폐기, `generation-failed(EVIDENCE_INVALIDATED)` · `409 DEADLINE_EXCEEDED` → 폐기(watchdog 이 처리) · `409 IDEMPOTENCY_CONFLICT` → 버그, 알림 · `422 INVALID_DRAFT` → 작업 5 보정 경로 · `401/403` → job fail, 알림 · `5xx` → 재전송 |
| 응답 검증 | `CaseSnapshot`·`BeginGenerationResult{fixed_sentencing|null, text_version, deadline_at}`·`FinalizeResult{verdict_id, text_version, committed_at}` 를 pydantic 으로 검증. 알 수 없는 필드 거부 |
| 모델 재호출 금지 | finalize 응답 유실은 **모델 실패가 아니다**(proposal2 §14.2·§15). 재전송 → 실패면 job fail. 다음 시도에서 `node_results` 재사용(작업 6) |

### 3.3 M2 스텁 핸들러 (`application/*`, `workers/dispatch.py` 교체)
| kind | 동작 | 결과 |
|---|---|---|
| `SENTENCE` | `begin-generation` → `generation-failed(AI_NOT_READY)` → `complete` | 백엔드가 `fallback_sentence` FINAL + 템플릿 + RETAIN. **TEXT_RETRY 예약 안 함**(오류 코드 표) |
| `TEXT_RETRY` | 같은 흐름(`AI_NOT_READY`) | 스텁 기간에는 백엔드가 만들지 않으므로 방어용 |
| `PREPARE` | `complete` 만(no-op) | `trial_prep` 을 쓰지 않는다. 작업 5 가 인라인 조서로 복구 |
| `RETAIN` | `complete` 만(no-op) | 작업 4 가 교체 |
- 스텁도 heartbeat·generation·`complete` 소유 조건을 그대로 탄다 — 작업 2 의 경합 테스트가 스텁으로 돈다

### 3.4 intake 스텁·인증
- `POST /internal/v1/intake` → `IntakeResult{status=PASS, item_review{status=OK, suggested_item=null}, message=null, category_review{OK}, injection_detected=false, intake_source=FALLBACK}` 50ms 이내. `mode=FINAL_CHECK` 도 `PASS`
- `api/auth.py`: `SERVICE_AUTH_TOKEN` 상수 비교(timing-safe). 불일치 401. `/health/*` 무인증. 인증 헤더는 로그에 남기지 않는다
- 제출 단위 임시 예산(proposal2 §7.4)은 작업 6 에서. 스텁은 `llm_calls` 를 쓰지 않는다

### 3.5 벤더 클라이언트 최소 구현 (`adapters/openai_compat_llm.py`)
| 항목 | 값 |
|---|---|
| 클라이언트 | `openai.AsyncOpenAI` 벤더별 1개. xAI 는 `base_url=XAI_BASE_URL`. `max_retries=0`(재시도는 그래프·예산이 결정) |
| 호출 | `response_format={"type":"json_schema","json_schema":{"name":role,"strict":true,"schema":schema}}`, `max_tokens=max_output_tokens`, `timeout=timeout_s` |
| 결과 | `LLMResult`: `stop_reason`(`finish_reason` → stop/max_tokens/refusal), usage(prompt·completion·reasoning·cached), cost(xAI `cost_in_usd_ticks` → micro-USD **내림** + tick 원값 / 없으면 단가표 `source="table"`), `provider_request_id`, `model_id`, `vendor` |
| 오류 | `LLMError(kind)`: `TIMEOUT`·`RATE_LIMIT`(429)·`SERVER`(5xx)·`TRANSPORT`·`REFUSAL`·`SCHEMA`(strict 거절)·`PARSE`. 세분 분류·백오프·예산 연동은 작업 6 `domain/retries.py` |
| 검증 | startup: 서기·드립 모델이 non-reasoning 인지(`grok-4.20-0309-non-reasoning` 허용 목록), 키 존재 |
| 단가표 | luna 0.20/1.20 · terra 2.00/12.00 · grok-4.20 1.25/2.50 ($/1M, 2026-09-07). KRW 1,450원/$ |

### 3.6 luna 실측 (`scripts/probe_writer_latency.py --provider openai --role …`)
| 역할 | 프롬프트 | 입력 | n | 판정 |
|---|---|---|---:|---|
| `intake` | 작업 7 초안(카테고리 enum·판정 기준·다른 사건 예시) | 택시 '무엇을' 3종(정상·과장·인젝션) | 5 | p90 ≤ 2.5초 |
| `sentencing` | 작업 5 초안(밴드·허용 목록 enum·근거 라벨) | 택시 `CASE` + `jury-guilty-75` | 5 | **p90 ≤ 2.0초** — 초과 시 D-23 |
| `evaluator` | `prompts/evaluator/guardrail-v2.md` 초안 + 검사표 | 택시 초안 2강도 | 5 | p90 ≤ 3.0초, 재현율은 작업 6 |
- 결과 `scripts/probe_out/<ts>-openai-<role>.json`(기존 형식). p50/p90·비용·`cached_tokens` 기록. `SENTENCING_NODE_TIMEOUT_SECONDS`·`EVALUATOR_NODE_TIMEOUT_SECONDS` 초기값(3·4)을 실측 p90 + 0.5초로 조정해 `00-INDEX.md` §7·§8.4 에 기록

### 3.7 가짜 백엔드 (`tests/fakes/backend_app.py`)
- FastAPI 앱 하나가 10 §4 의 5 API 를 **최소 상태 기계**로 구현: verdict 별 `sentence_status`·`text_status`·`text_version`·`active_generation_id`·commit record dict. `begin-generation` 409 규칙, `finalize` 의 hash·버전·generation 검사와 commit record 멱등, `generation-failed` 오류 코드 표, `snapshot` fixture 반환, `resolve-evidence` fixture 반환
- 작업 4·5·8 의 통합 테스트가 이 가짜를 쓴다. 실제 백엔드와의 대조는 CT-08 성격의 M2 시연에서

## 4. 완료 기준 (DoD)

### 4.1 정량 목표
| 지표 | 목표 | 측정 |
|---|---|---|
| M2 수직 흐름 | 등록 → 2인 투표 → 전원 투표 즉시 확정 → **템플릿 노출 ≤ 2초**(watchdog 10초 아님), 형량 FINAL 1회 | 실제 백엔드 + 우리 워커 |
| 워커 중단 복구 | job RUNNING 중 SIGKILL → reaper 회수 → 재claim → 새 generation 으로 완료. 이전 generation 의 `generation-failed` 는 409 | `test_worker_restart.py` |
| finalize 재전송 | 같은 본문 2회 → 200 동일 `text_version`, 다른 본문 → 409 IDEMPOTENCY_CONFLICT | `test_backend_http.py` + 가짜 백엔드 |
| 스텁 지연 | intake 스텁 p95 < 50ms, SENTENCE 스텁 claim→complete < 500ms | 로컬 100회 |
| luna 실측 | 3역할 × n=5 파일 + 판정 기록 | `scripts/probe_out/` |

### 4.2 검증 테스트 시나리오
- **`tests/unit/test_backend_http.py`**(httpx `MockTransport`)
  - [ ] 헤더 5종 부착, 토큰이 로그에 없음
  - [ ] transport 오류 → 같은 본문 재전송 2회 → 실패 시 `BACKEND_UNAVAILABLE`
  - [ ] 409 4종·422·401 매핑, 알 수 없는 응답 필드 거부
- **`tests/integration/test_m2_vertical.py`**(가짜 백엔드 + 실제 Postgres + 워커)
  - [ ] SENTENCE job → begin → failed(AI_NOT_READY) → 가짜 백엔드 상태 `FINAL/RULE + TEMPLATE_READY`, TEXT_RETRY 없음, RETAIN job INSERT 됨
  - [ ] PREPARE·RETAIN no-op 성공
  - [ ] intake 스텁 `PASS`, 인증 없음 401
- **`tests/integration/test_worker_restart.py`**: RUNNING 중 프로세스 kill → reaper → 재claim → 이전 generation 호출 409 → 새 generation 성공
- **실측**: `probe --provider openai --role sentencing --n 5` 등 3회 실행, 결과 파일 커밋

### 4.3 동작 확인 가이드 (수동)
```bash
uv run uvicorn tests.fakes.backend_app:app --port 8200 &        # 가짜 백엔드
BACKEND_INTERNAL_URL=http://localhost:8200 uv run geoji-ai worker --reaper &
uv run scripts/enqueue_job.py --kind SENTENCE --verdict v1 --version 1 --post p1
curl -s localhost:8200/posts/p1/verdict | jq '.sentence_status, .text_status, .view.source'
uv run scripts/probe_writer_latency.py --provider openai --role sentencing --n 5
```

### 최종 완료 기준:
- [ ] 실제 백엔드에서 **AI 호출 없이 등록 → 투표 → 템플릿 노출, 형량 1회 확정**(proposal2 §20 작업 3)
- [ ] 가짜 백엔드가 5 API 계약을 재현하고 작업 4·5 테스트 기반이 됨
- [ ] luna 3역할 실측 기록 + 노드 타임아웃 조정 + D-23 판정 기록(`00-INDEX.md` §8.4)
- [ ] `generation-failed` 오류 코드 표를 백엔드가 채택(10 §4.6)

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| VF-01 | 가짜 백엔드 | 5 API 최소 상태 기계, commit record, 오류 코드 표 | test | 0.5d | 01 |
| VF-02 | `backend_http.py` | 5 호출, 헤더, 타임아웃, 멱등 재전송, 오류 매핑, 응답 검증 | adapter | 0.5d | 01 CT-06 |
| VF-03 | 스텁 핸들러 4종 + intake 스텁·인증 | dispatch 교체, `AI_NOT_READY` 흐름, `/internal/v1/intake` | worker·api | 0.5d | 02 JQ-04, VF-02 |
| VF-04 | `openai_compat_llm.py` 최소 + 설정 검증 | 두 벤더, strict, usage·cost, 오류 kind | adapter | 0.5d | 01 |
| VF-05 | luna 실측 | 스크립트 확장, 3역할 n=5, 타임아웃 조정, D-23 판정 | eval | 0.25d | VF-04 |
| VF-06 | M2 시연 | 실제 백엔드와 수직 흐름·워커 중단 복구 확인 | integration | 0.25d | VF-03, 백엔드 |

**VF-01** — [ ] 상태 기계 / [ ] begin 409 규칙 / [ ] finalize 멱등·hash 검사 / [ ] failed 코드 표 / [ ] snapshot·resolve fixture
**VF-02** — [ ] 5 메서드 / [ ] 헤더·타임아웃 / [ ] 재전송 2회 / [ ] 오류 매핑 / [ ] pydantic 응답
**VF-03** — [ ] SENTENCE·TEXT_RETRY 스텁 / [ ] PREPARE·RETAIN no-op / [ ] intake 라우트·auth / [ ] `test_m2_vertical`
**VF-04** — [ ] 벤더별 클라이언트 / [ ] strict 호출 / [ ] usage·ticks → micro-USD / [ ] `LLMError` kind / [ ] startup 검증
**VF-05** — [ ] `--role` 3종 / [ ] 결과 파일 / [ ] INDEX 기록
**VF-06** — [ ] 실제 백엔드 시연 / [ ] kill·재기동 확인
