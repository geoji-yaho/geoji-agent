# 백엔드 전달 사항

AI 파트(`geoji-agent`)가 백엔드 담당에게 알려야 하는 것을 모아 둔다. 계약 자체는 `docs/plans/10-backend-contract.md`(10)에 있고, 이 파일은 **그 계약을 실제로 붙이고 띄울 때 알아야 하는 것**만 담는다. 환경변수, 엔드포인트, 복사해 갈 파일, 바뀐 값, 우리가 백엔드에 기다리는 회신.

읽는 법. 위가 최신이다. 상태가 `미전달` 인 줄을 백엔드에 보내고 `전달 9/NN` 으로 바꾼다. 백엔드가 반영하면 `반영` 으로.

## 1. 배포 환경변수 (EC2 compose 에 넣을 것)

`ai-api`·`ai-worker` 컨테이너 둘 다 같은 목록을 받는다. 키 이름 전체는 저장소 루트 `.env.example`, 기본값·설명은 `README.md` 설정 표. 여기는 **직접 적어야 하는 것**만.

| 키 | 값 | 없으면 | 비고 |
|---|---|---|---|
| `APP_ENV` | `production` | 개발 모드로 뜬다(기동 검사 느슨) | 9/11 확정 |
| `GUARDRAIL_POLICY_VERSION` | `guardrail-v2` | **기동 실패.** production 은 이 값을 환경에 직접 적어야 한다 | 01 §3.7 ①, 10 §15.3 D-07. 팀 비준 뒤 `v1` 로 내릴 수 있다 |
| `OPENAI_API_KEY` · `XAI_API_KEY` | AI 파트가 전달 | 뜨긴 하나 `/health/ready` 가 503 | 10 §15.3 키·결제. 로그 금지 |
| `DATABASE_URL` | Supabase Session Pooler 접속 문자열(`ai_api`/`ai_worker` role) | 작업 2 부터 `/health/ready` 503 | 10 §2, D-20. `postgresql://` 그대로 줘도 된다(어댑터가 `+asyncpg` 로 바꾼다) |
| `BACKEND_INTERNAL_URL` | 백엔드 내부 API 베이스 URL | 작업 3 부터 **워커 기동 실패**(비어 있으면) | 10 §4 |
| `SERVICE_AUTH_TOKEN` | 내부 API 서비스 토큰(양쪽 같은 값) | 작업 3 부터 401. AI API 는 값이 비면 전부 401(열어 두지 않음) | 10 §4 "서비스 인증 필수" |
| `ALERT_DISCORD_WEBHOOK_URL` | 팀 디스코드 웹훅 | 알림 없음 | 10 §15.3 |

## 2. 엔드포인트

| 경로 | 응답 | 용도 | 시점 |
|---|---|---|---|
| `GET /health/live` | 200 `{"status":"ok"}` | 프로세스 생존. compose healthcheck 에 쓴다. 무인증 | 지금 |
| `GET /health/ready` | 200 또는 503 `{"status":"not_ready","missing":[키 이름]}` | 키·DB 준비 여부. 503 이면 트래픽을 보내지 않는다. 무인증 | 지금(키), 작업 2(DB) |
| `POST /internal/v1/intake` | `IntakeResult` | 심문관. 백엔드가 호출. `Authorization: Bearer <SERVICE_AUTH_TOKEN>` | 작업 3 스텁(§4 9/14 줄), 작업 7 실제 |

## 3. 복사해 갈 파일

| 파일 | 어디에 | 왜 |
|---|---|---|
| `database/migrations/001_ai_jobs.sql`·`002_preparation_evidence.sql`·`003_memory_call_ledger.sql` | 적용만(복사 불필요) | `DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate` 로 우리가 적용한다. 번호 순·`ai.schema_migrations` 기록·재적용 no-op·advisory lock 으로 동시 실행 안전. **001~003 만** 적용하고 004 는 백엔드 소유. **파일에 `CREATE ROLE` 이 없다** — `ai_worker`·`ai_api`·`backend` role 을 먼저 만들어야 `GRANT` 가 통과한다. 권한 표는 각 SQL 파일 끝 |
| `src/geoji_ai/adapters/postgres_jobs.py` 의 `REAPER_SQL` | 백엔드 스케줄러, 5초 주기 | 02 §3.5 원문 그대로. 운영에서 워커 `--reaper` 는 끈다. TEXT_RETRY `FAILED` 뒤 다음 round 예약과 SENTENCE `CANCELLED` 뒤 폴백은 백엔드 watchdog 몫(10 §7) |
| `contracts/fixtures/templates-v1.json` | 백엔드 저장소, 버전 고정 | watchdog·generation-failed 폴백 문구(10 §10). 치환 토큰 `{n}`·`{m}`·`{sentence_label}` (9/11 `{형량 라벨}` → `{sentence_label}` 로 바뀜) |
| `contracts/*-v1.schema.json` 7종 | 참조 | 계약 정본. enum 은 프론트 값(`mild/spicy/hell`, `guilty/notGuilty/agree/disagree/dismissed`, `probation/oneDay/life`, `spent/considering`). 서버의 대문자 enum 은 백엔드가 맞춘다(10 §15.2 D-21) |

## 4. 계약에서 백엔드가 알아야 할 것

| 날짜 | 항목 | 내용 | 출처 | 상태 |
|---|---|---|---|---|
| 9/14 | RETAIN snapshot 필드 | `CaseSnapshot` 에 선택 필드 `verdict_final`(sentence.finalized)·`comment`(comment.approved). 기존 스냅샷은 그대로 유효, `schema_version` 1. `comment.content` 가 1000자를 넘으면 스냅샷 전체가 거부된다. 모양은 `contracts/case-snapshot-v1.schema.json` | 10 §4.1 | 미전달 |
| 9/14 | 002·003 grants | `backend` 는 무효화용 `evidence`·`dossiers`·`trial_prep`·`memory_facts`·`node_results` SELECT·UPDATE, `evidence_sources` SELECT. `ai_worker` 는 002·003 테이블 SELECT·INSERT·UPDATE, `ai_api` 는 `case_budgets`·`llm_calls` SELECT·INSERT·UPDATE + 스키마 USAGE. DELETE 는 아무에게도 없다 | 10 §1 | 미전달 |
| 9/14 | `privacy_epochs` 정의 가정 | AI 테스트는 004 의 `ai.privacy_epochs(scope_key text PRIMARY KEY, epoch bigint NOT NULL DEFAULT 0)` 에 `ai_worker` SELECT, `backend` SELECT·INSERT·UPDATE 를 가정한다. 행이 없으면 epoch 0 으로 본다. 004 초안이 다르면 회신(§5) | 10 §2 | 미전달 |
| 9/14 | `generation-failed` 오류 코드 처리 | 가짜 백엔드가 10 §4.6 표 그대로 구현: `AI_NOT_READY`·`POLICY_ERROR`·`EVIDENCE_INVALIDATED` → 즉시 폴백(FINAL/RULE·TEMPLATE_READY·RETAIN), `VENDOR_UNAVAILABLE`·`BUDGET_EXCEEDED`·`EVAL_FAILED`·`SCHEMA_INVALID`·`DEADLINE_EXCEEDED` → 같은 폴백 + TEXT_RETRY round 1. 채택 회신은 §5 | 10 §4.6·§14 | 미전달 |
| 9/14 | 내부 API 5종 상태 기계(가짜 백엔드 기준) | begin: SENTENCE 는 PENDING·마감 전, 같은 generation 재호출 허용, 다른 활성 generation·`verdict_version` 불일치 → 409 `STALE_GENERATION`, FINAL 이면 `fixed_sentencing`. generation-failed: 현재 세대만(다른 세대 409), 같은 세대·같은 코드 재전송 200. finalize: commit record(`sha256(본문)`) 일치 → 같은 200 / 불일치 → 409 `IDEMPOTENCY_CONFLICT` → generation·버전·`expected_text_version` → hash 형식·`guardrail_policy_version` → 저장. snapshot: job ∧ `X-Generation-Id` 일치 아니면 409. 실제 구현이 다르면 알려 달라 | 10 §4.1·§4.3·§4.6·§5 | 미전달 |
| 9/14 | 거부 응답 본문 모양 | 가짜 백엔드는 `{"code": "<코드>"}`. 워커 `BackendHttp` 가 이 `code` 를 읽는다(없으면 `HTTP_<status>`). 401 `UNAUTHORIZED`·404 `NOT_FOUND`·422 `INVALID_REQUEST` 는 10 에 없음 — 확정 필요(§5) | 10 §4·§5 | 미전달 |
| 9/14 | RETAIN `sentence.finalized` payload `version` | `version = verdict_version` 으로 INSERT(dedupe `retain:verdict:{verdict_id}:{version}`) | 02 §3.4, 10 §3 | 미전달 |
| 9/14 | intake 스텁 응답 | `POST /internal/v1/intake` 는 M2 동안 항상 `{schema_version:1, mode:<요청>, status:PASS, item_review{OK,null}, message:null, category_review{OK,null,1.0}, injection_detected:false, intake_source:FALLBACK}` | 03 §3.4, 10 §4 | 미전달 |
| 9/14 | 서비스 인증 | 양방향 `Authorization: Bearer <SERVICE_AUTH_TOKEN>`. `/health/*` 만 무인증 | 03 §3.3·§3.4 | 미전달 |
| 9/14 | 워커 → 백엔드 헤더·타임아웃 | 헤더 5종 `Authorization`·`X-Trace-Id`(job.trace_id)·`X-Request-Id`(호출마다 uuid4)·`X-Job-Id`·`X-Generation-Id`. 타임아웃 connect 0.5s, snapshot 2·resolve 2·begin 1·finalize 3·failed 1s. transport·timeout·5xx 에 같은 본문 2회 재전송 | 03 §3.2 | 미전달 |
| 9/11 | `ai.jobs` 권한 | `ai_worker` = 스키마 `USAGE` + `SELECT, UPDATE`(**INSERT 없음**), `backend` = `USAGE` + `INSERT, SELECT, UPDATE`, `ai_api` = `ai.jobs` 권한 없음 | 02 §3.1, 10 §1·§2 | 미전달 |
| 9/11 | job INSERT 규약 어긋남 | `scripts/enqueue_job.py` 가 02 §3.4 = 10 §3 규약(priority 100/50/30/10/10, max_attempts 2/1/2/5/5, dedupe 5종, SENTENCE·TEXT_RETRY 만 deadline)대로 넣는 것을 확인. **어긋남 2건**: ① RETAIN payload 는 `{event, verdict_id\|comment_id, version}` 이다(10 §3 의 `verdict_version`·`comment_version` 이 아니다) ② `TEXT_RETRY payload.intensities[]` 는 백엔드 미채택이라 넣지 않았다 | 02 §3.4, 10 §3·§14 | 미전달 |
| 9/11 | `CaseSnapshot` 모양 | **평면**이다. `post_id`·`author_id`·`item`… 이 최상위. 10 §4.1 의 `post{…}` 중첩이 아니라 01 §3.2 를 따른다 | 01 §3.2, 10 §4.1 | 미전달 |
| 9/11 | `guilty_ratio` | 0..1 소수(`0.75`). 백분율 아님 | 10 §4.1 | 미전달 |
| 9/11 | `SentencingDecision.reason_source`·`TextDraft.source` | finalize 본문에 들어간다. 둘 다 `AI \| TEMPLATE` 필수 | 00 §8.3, 10 §5 | 미전달 |
| 9/11 | `IntakeResult.mode` | 결과에 `mode`(`INITIAL \| FINAL_CHECK`)가 들어간다. `FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 내지 않는다 | 01 §3.2 | 미전달 |
| 9/11 | 길이·배열 상한 | 계약에 없던 상한을 채웠다(라벨 ≤16·`^F\d+$`, `statement` 1~300자 등). 목록은 01 §3.2 공통 규칙 | 01 §3.2 | 미전달 |

## 5. 백엔드 회신을 기다리는 것

| 항목 | 무엇 | 기한 | 출처 |
|---|---|---|---|
| `privacy_epochs` 004 정의 | §4 9/14 줄의 테이블 정의·권한 가정이 004 초안과 같은가 | 미정 | 10 §2 |
| 거부 응답 본문 | 401·404·409·422 본문이 `{"code": …}` 인가, 404·422 코드 이름 | 미정 | 10 §4·§5 |
| 내부 API 상태 기계 | §4 9/14 줄(가짜 백엔드 기준)과 실제 구현이 같은가 | 미정 | 10 §4·§5 |
| `CaseSnapshot` 필드 | `post_version`·`audience_version`·`privacy_versions`·`rule_version`·`policy{…}`, RETAIN 확장 `verdict_final`·`comment` 를 채울 수 있는가. 9/11 서버(`d0f9fd5`)에 전부 없음 | 9/9(지남) | 10 §14 |
| `ai` 스키마 role | `ai_api`·`ai_worker`·`backend` role 생성·grants, Session Pooler 접속 정보 | 9/9(지남) | 10 §14 D-20 |
| §4.6 오류 코드 표 채택 | `generation-failed` 의 `error_code` 표(§4 9/14 줄대로 구현됨) | 9/10(지남) | 10 §14 |
