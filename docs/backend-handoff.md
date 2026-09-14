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
| `POST /internal/v1/intake` | `IntakeResult` | 심문관. 백엔드가 호출. `Authorization: Bearer <SERVICE_AUTH_TOKEN>`. 실제 모델 호출·폴백·422 규칙은 §4 9/14 intake 라우트 줄 | 지금(작업 7 실제) |
| `GET /internal/v1/metrics/snapshot` | JSON(지표마다 `source: process\|db`) | 운영 지표 스냅샷. 서비스 인증 | 지금 |
| `GET /internal/v1/trials/{post_id}/trace` | JSON(라벨·코드·개수·시각, 원문 없음). 기록 없으면 404 `TRACE_NOT_FOUND` | 데모 C 내부 trace 화면. 서비스 인증. 프록시 주체는 §5 | 지금 |

## 3. 복사해 갈 파일

| 파일 | 어디에 | 왜 |
|---|---|---|
| `database/sql/invalidate_scope.sql` | 백엔드 무효화 스케줄러 | 삭제·공유 철회 뒤 파생 데이터 무효화 SQL(04 §3.5, 10 §8). **한 트랜잭션**으로 실행, 바인드 `:t`·`:id`·`:scope_key`. `node_results` 조건은 `@>`(04 원문 `?` 는 객체 배열에서 매치 안 됨) |
| `scripts/seed_memory_demo_c.py` | 실행만(우리 저장소) | 데모 C 메모리 시드. 백엔드 시드가 만든 스타벅스 post 2개·verdict 2개 id 를 받아 `uv run scripts/seed_memory_demo_c.py --user U --room R --post-ids P1,P2 --verdict-ids V1,V2 [--now ISO8601]`. 멱등. `--now` 는 백엔드 시드 기준 시각과 맞춘다(04 §3.6, 10 §12) |
| `database/migrations/001_ai_jobs.sql`·`002_preparation_evidence.sql`·`003_memory_call_ledger.sql` | 적용만(복사 불필요) | `DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate` 로 우리가 적용한다. 번호 순·`ai.schema_migrations` 기록·재적용 no-op·advisory lock 으로 동시 실행 안전. **001~003 만** 적용하고 004 는 백엔드 소유. **파일에 `CREATE ROLE` 이 없다** — `ai_worker`·`ai_api`·`backend` role 을 먼저 만들어야 `GRANT` 가 통과한다. 권한 표는 각 SQL 파일 끝 |
| `src/geoji_ai/adapters/postgres_jobs.py` 의 `REAPER_SQL` | 백엔드 스케줄러, 5초 주기 | 02 §3.5 원문 그대로. 운영에서 워커 `--reaper` 는 끈다. TEXT_RETRY `FAILED` 뒤 다음 round 예약과 SENTENCE `CANCELLED` 뒤 폴백은 백엔드 watchdog 몫(10 §7) |
| `contracts/fixtures/templates-v1.json` | 백엔드 저장소, 버전 고정 | watchdog·generation-failed 폴백 문구(10 §10). 치환 토큰 `{n}`·`{m}`·`{sentence_label}` (9/11 `{형량 라벨}` → `{sentence_label}` 로 바뀜) |
| `geoji-ai ledger-sweep --older-than 24h` | 실행만(이미지 명령) | 결과 불명(UNKNOWN) 호출의 예약액을 확정한다. 백엔드 스케줄러 또는 cron 하루 1회, `DATABASE_URL` 필요(08 §3.2) |
| `scripts/seed_agent_db.py` | 실행만(우리 저장소) | 데모 시드(드립 예시·데모 C 메모리). 멱등. 인자는 `--help`(08 §3.5). `meme_catalog` 은 백엔드 `meme_images` 소유라 만들지 않는다 |
| `docs/runbook.md` | 참조 | 헬스·알림·비용 경고·롤백·동결·토큰 회전 절차(08 §3.6). 리허설 체크리스트 포함 |
| `contracts/*-v1.schema.json` 7종 | 참조 | 계약 정본. enum 은 프론트 값(`mild/spicy/hell`, `guilty/notGuilty/agree/disagree/dismissed`, `probation/oneDay/life`, `spent/considering`). 서버의 대문자 enum 은 백엔드가 맞춘다(10 §15.2 D-21) |

## 4. 계약에서 백엔드가 알아야 할 것

| 날짜 | 항목 | 내용 | 출처 | 상태 |
|---|---|---|---|---|
| 9/14 | ledger-sweep RESERVED 정리 | `--older-than` 보다 오래된 `RESERVED` 호출도 `UNKNOWN` 으로 바꿔 예약액을 spent 로 확정한다(취소·강제 종료로 남은 예약). 출력에 RESERVED 건수 추가. 크론 설정은 바꿀 것 없음 | 08 §3.2, 06 §3.2 | 미전달 |
| 9/14 | `generation-failed` 코드 분포 | 서기가 전부 시간 초과로 실패하면 `VENDOR_UNAVAILABLE` 대신 `DEADLINE_EXCEEDED`. 둘 다 TEXT_RETRY round 예약 코드라 분기는 같고 집계 분포만 바뀐다 | 08 §3.5, 10 §4.6 | 미전달 |
| 9/14 | 노드 timeout 환경변수 | `*_NODE_TIMEOUT_SECONDS`·`INTAKE_TIMEOUT_SECONDS` 가 소수를 받는다(기본값 불변) | 08 §3.5 | 미전달 |
| 9/14 | watchdog 뒤 job 상태 | 늦은 성공 뒤 이전 job 상태가 08 §3.1 `complete` 와 10 §6-4 `CANCELLED` 로 어긋난다. 우리 테스트는 `CANCELLED`(워커 complete 0행)로 고정. 실제 구현 확인(§5) | 10 §6, 08 §3.1 | 미전달 |
| 9/14 | finalize `meme_hints.emotion` enum | writer-draft-v1·finalize-v1 의 `meme_hints.emotion` 이 문자열(≤30)에서 6종 enum(`DISAPPROVAL`·`ABSURD_SERIOUSNESS`·`SMUG`·`PITY`·`CELEBRATION`·`RESIGNATION`)으로 좁혀짐, `schema_version` 1. 짤 점수 `+2 감정 일치` 대조값. 스키마 복사본 갱신 | 08 §3.4, 01 §6.1 | 미전달 |
| 9/14 | `lease_expired_total` | 워커는 관측하지 않는다. 운영 reaper(백엔드, 02 §3.5 `REAPER_SQL`)가 회수 건수를 kind 별로 남겨야 지표가 생긴다 | 08 §3.3 | 미전달 |
| 9/14 | 형량 규칙 위반 422 코드 | finalize 422 가 `INVALID_DRAFT` 하나라 형량 규칙 위반 알림을 가를 수 없다. 별도 코드 필요 여부 회신(§5) | 08 §3.3, 10 §5 | 미전달 |
| 9/14 | 10 §4.4 강도 집합 검증 | TEXT_RETRY finalize 에서는 "강도 집합 = target_intensities" 를 **⊆ target** 으로 완화 필요. 받은 강도만 갱신, 나머지 강도 기존 행 유지 | 10 §4.4·§4.5 10번 | 미전달 |
| 9/14 | TEXT_RETRY payload `intensities[]` | 워커가 선택 필드로 받는다. 없으면 target 전체. 일부 강도만 TEMPLATE 이면 그 강도만 보내 달라. target 밖 강도면 `generation_failed(SCHEMA_INVALID)` | 10 §3·§4.5 10번 | 미전달 |
| 9/14 | TEXT_RETRY 실패 시 저장 없음 | round 안 서기·검증·검수 실패는 TEMPLATE finalize 없이 `generation_failed`(`EVAL_FAILED`·`DEADLINE_EXCEEDED` 등)만. 다음 round 예약은 백엔드 | 08 §3.2 | 미전달 |
| 9/14 | `ledger-sweep` 실행 주기 | §3 `geoji-ai ledger-sweep` 를 하루 1회. 스케줄은 우리가 만들지 않았다 | 08 §3.2 | 미전달 |
| 9/14 | `ai.llm_calls` UNKNOWN 정리 표시 | 정리된 행은 `status='UNKNOWN'` 그대로 `actual_micro_usd = estimated_max_micro_usd`. 집계 시 `UNKNOWN ∧ actual NOT NULL` = 정리됨 | 08 §3.2, 003 | 미전달 |
| 9/14 | intake 라우트 실제 동작 | 스텁이 아니라 OpenAI `MODEL_JUDGMENT` 를 부른다. 키 없음·실패·timeout(4초 − 0.2) → `PASS`·`FALLBACK`·`message=""`·`category_review.confidence=0.0`(스텁은 `null`·`1.0`). 강한 인젝션·무관 텍스트는 모델 없이 `BLOCKED`. 필수값 위반 422 `detail={"code": ITEM_LENGTH\|REASON_LENGTH\|AMOUNT}` | 07 §3.1·§3.2 | 미전달 |
| 9/14 | 제출 임시 예산 | `DATABASE_URL` 이 있으면 intake 호출이 `ai.case_budgets`·`ai.llm_calls` 에 `post_id='submission:{id}'` 로 기록, cap 1,034 micro-USD. 초과 시 `PASS`·`FALLBACK`(등록 허용) | 07 §3.4 | 미전달 |
| 9/14 | intake 응답 에코 없음 | 응답에 `submission_id`·`payload_hash` 가 없다(01 계약에 필드 없음, 07 §3.1 과 어긋남). 요청·응답 묶기는 백엔드가 요청 쪽에서 | 07 §3.1, 01 | 미전달 |
| 9/14 | 002 DDL `ai.evidence.fact_type` | 조서 kind `REASON_ANALYSIS` 가 CHECK 에 없어 저장하지 않는다. DDL 을 넓힐지 05 §3.2 를 줄일지 결정 뒤, 넓히면 마이그레이션 반영 필요 | 05 §3.2, 002 | 미전달 |
| 9/14 | `draft_hash` canonical 규칙 | finalize 가 `draft_hash`·`evaluation_draft_hash` 를 같은 규칙으로 재계산해야 한다: `{"draft": WriterDraft, "sentencing": SentencingDecision 또는 null}` → 키·값 문자열 NFC → 키 정렬·구분자 `,` `:`·비 ASCII 이스케이프 없음 JSON → UTF-8 sha256 소문자 hex. 정의는 `src/geoji_ai/domain/draft_hash.py` | 05 §3.4, 10 §5 | 미전달 |
| 9/14 | RETAIN snapshot 확장 필드 없을 때 | `verdict_final`·`jury`(sentence.finalized)·`comment`(comment.approved)가 없으면 워커는 행 0 으로 complete — **기억이 쌓이지 않는다**. 댓글 방이 `room_snapshots` 에 있어야 댓글 기억 저장 | 04 §3.3, 10 §4.1 | 미전달 |
| 9/14 | RETAIN snapshot 404 규약 | RETAIN 원본(판결·댓글)이 삭제됐으면 snapshot 이 **404** 를 줘야 워커가 skip. 409 등은 skip 이 아니라 오류로 재시도 | 04 §3.5, 10 §4.1 | 미전달 |
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
| watchdog 뒤 job 상태 | 늦은 성공 뒤 이전 job 을 `CANCELLED`(10 §6-4)로 두는가, `complete`(08 §3.1)인가 | 미정 | 10 §6, 08 §3.1 |
| 형량 규칙 위반 422 코드 | `INVALID_DRAFT` 외에 형량 규칙 위반 전용 코드를 둘 수 있는가 | 미정 | 10 §5 |
| TEXT_RETRY 강도 집합 | finalize 검증을 TEXT_RETRY 에서 `⊆ target_intensities` 로 완화하고 `intensities[]` 를 payload 에 넣어 줄 수 있는가 | 미정 | 10 §3·§4.4 |
| trace 프록시 주체 | `/internal/v1/trials/{post_id}/trace` 를 프론트에 누가 중계하는가 | 미정 | 10 §4.5·§14 |
| `lease_expired_total` | 운영 reaper 회수 건수를 kind 별 로그·지표로 남길 수 있는가 | 미정 | 08 §3.3 |
| `aggregates.burn_rate` 단위 | 0~1 비율인가(우리는 0~1 로 가정해 문장을 만든다) | 미정 | 10 §4.2 |
| `recent_verdicts[].verdict_id` | 응답에 `verdict_id` 가 없어 PRIOR 근거 출처를 `POST/{post_id}/{post_version}` 로 둔다. `verdict_id`·`verdict_version` 을 넣어 줄 수 있는가 | 미정 | 10 §4.2 |
| 데모 C 시드 식별자 | 백엔드 시드의 스타벅스 post 2개 `post_id`·`verdict_id`, 사용자·방 id, 시드 기준 시각(§3 시드 스크립트 입력) | 9/17 | 10 §12 |
| `privacy_epochs` 004 정의 | §4 9/14 줄의 테이블 정의·권한 가정이 004 초안과 같은가 | 미정 | 10 §2 |
| 거부 응답 본문 | 401·404·409·422 본문이 `{"code": …}` 인가, 404·422 코드 이름 | 미정 | 10 §4·§5 |
| 내부 API 상태 기계 | §4 9/14 줄(가짜 백엔드 기준)과 실제 구현이 같은가 | 미정 | 10 §4·§5 |
| `CaseSnapshot` 필드 | `post_version`·`audience_version`·`privacy_versions`·`rule_version`·`policy{…}`, RETAIN 확장 `verdict_final`·`comment` 를 채울 수 있는가. 9/11 서버(`d0f9fd5`)에 전부 없음 | 9/9(지남) | 10 §14 |
| `ai` 스키마 role | `ai_api`·`ai_worker`·`backend` role 생성·grants, Session Pooler 접속 정보 | 9/9(지남) | 10 §14 D-20 |
| §4.6 오류 코드 표 채택 | `generation-failed` 의 `error_code` 표(§4 9/14 줄대로 구현됨) | 9/10(지남) | 10 §14 |
