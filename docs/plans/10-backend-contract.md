# 🤝 [Contract] 백엔드 계약서 — 공유 Postgres · `ai.jobs` INSERT · 내부 API · finalize · watchdog · 재시도 · 삭제 epoch · 공개 API · 배포·연동

> 9/20 정리판. 9/19 까지의 전달 사항·미결·날짜별 정정은 전부 반영됐고 원문은 `archive/10-backend-contract-2026-09-19.md` 에 있다. 여기는 **지금 코드가 하는 대로**만 적는다. `(원문)`·`(제안)`·날짜별 정정 문장은 뺐다.
> 9/20 이후 새 전달 사항은 이 문서를 고치지 않고 **날짜별 전달 문서(19, 20, …)** 에 적는다. 이 문서는 상시 계약만 담는다. 첫 전달 문서는 **19**(데모 AI 배심원 떼거지봇).

## 0. 읽는 법

| 절 | 내용 |
|---|---|
| §0.1 | 전달 문서 목록 |
| §1 | 전제·아키텍처·role·grants·공통 규칙 |
| §2 | DB — `ai` 스키마 소유, 004 업무 테이블, 잠금 순서 |
| §3 | 업무 트랜잭션에서 `ai.jobs` INSERT 규약 |
| §4 | 내부 API — intake · snapshot · resolve-evidence · begin-generation · generation-failed · §4.7 공통 규약 |
| §5 | finalize 12단계·결과 코드·`draft_hash`·카드 규격 |
| §6 | deadline watchdog · D-24 SENTENCE 게이트 |
| §7 | 재시도 round · reaper |
| §8 | 삭제·권한 변경 — privacy epoch·무효화·D-26 |
| §9 | 공개 API |
| §10 | 템플릿 공유 파일 |
| §11 | 짤 점수 선택 |
| §12 | 시드 |
| §13 | 백엔드 수용 검사 |
| §16 | **배포·연동** — 환경변수 · AI API 엔드포인트 · 복사해 가거나 실행할 파일 |

- 절 번호는 인용 ID 다(`10 §4.7`). 9/19 판과 번호를 같게 유지했다. §14(미결)·§15(날짜별 결정 기록)는 archive 로 갔고 새 미결은 전달 문서의 미결 절에 적는다
- 역할 이름(심문관·조서·드립 후보·양형관·서기·검수관)이 헷갈리면 `docs/agent-roles.md`
- 값의 정본: 환경변수 키 이름은 AI 저장소 `.env.example`, 기본값·설명은 AI 저장소 `README.md` 설정 표, JSON 모양은 `contracts/*-v1.schema.json`. 이 문서는 "무엇을 어떻게 붙이는가" 를 적는다

### 0.1 전달 문서 목록

| 문서 | 내용 | 상태 |
|---|---|---|
| [19 데모 AI 배심원 떼거지봇](19-backend-handoff-ai-juror.md) | `JURY_VOTE` 잡 · snapshot 확장 · jury-votes 내부 API · ai-member 공개 API · 006 | 미전달 |
| [archive/10 (9/19 판)](archive/10-backend-contract-2026-09-19.md) §0.1 | 9/14~9/19 전달 21건 | 반영 |

## 1. 전제 · 아키텍처 · role

```
Frontend ──▶ Main Backend ── 내부 HTTP ──▶ AI API (FastAPI)  : 심문(그래프 A) · 관측(metrics·trace)
                │  게시물/투표/평결/권한 소유 · 결과 확정 API · watchdog · round 예약 · 삭제 무효화
                ▼
            Postgres (공유 Supabase 인스턴스, Session Pooler)
              ├─ app 영역: posts, post_rooms, votes, verdicts, verdict_texts, submissions, meme_images …
              └─ ai  영역: jobs, trial_prep, dossiers, evidence, evidence_sources, banter_examples,
                           memory_facts, processed_memory_events, case_budgets, llm_calls, node_results,
                           privacy_epochs, verdict_commit_records, text_evidence_refs
                     ▲ claim / heartbeat / 중간 결과
              Python Worker ── 그래프 B·C·D · retain · TEXT_RETRY ── OpenAI / xAI ── Backend 내부 API
```

- **워커는 업무 테이블을 직접 쓰지 않는다.** 최종 저장 권한은 백엔드 내부 API 에만 있다
- role 3종. 마이그레이션 파일에 `CREATE ROLE` 은 없다 — 먼저 만들어야 `GRANT` 가 통과한다. DELETE 는 어느 role 에도 없다

| role | 스키마 | `ai.jobs`(001) | 002 `trial_prep`·`dossiers`·`evidence`·`evidence_sources`·`banter_examples` | 003 `memory_facts`·`processed_memory_events`·`case_budgets`·`llm_calls`·`node_results` | 004(백엔드 소유) |
|---|---|---|---|---|---|
| `ai_api`(심문·관측) | USAGE | 없음 | 없음 | `case_budgets`·`llm_calls` SELECT·INSERT·UPDATE | 없음 |
| `ai_worker` | USAGE | SELECT·UPDATE(**INSERT 없음**) | 전부 SELECT·INSERT·UPDATE | 전부 SELECT·INSERT·UPDATE | `privacy_epochs` SELECT |
| `backend` | USAGE | INSERT·SELECT·UPDATE | `evidence`·`dossiers`·`trial_prep` SELECT·UPDATE, `evidence_sources` SELECT | `memory_facts`·`node_results` SELECT·UPDATE | 업무 테이블 전부 + `privacy_epochs`·`verdict_commit_records`·`text_evidence_refs` 쓰기 |

- 내부 API 인증은 `Authorization: Bearer <SERVICE_AUTH_TOKEN>`, **양방향**(백엔드 → AI API, 워커 → 백엔드). 헤더·타임아웃·거부 본문은 §4.7. 토큰은 로그에 남기지 않는다. 외부 사용자가 임의 `user_id`·`room_id` 로 조회하는 엔드포인트로 노출하지 않는다
- 공통 규칙: ID 는 문자열(신규 AI 테이블 UUID, 업무 ID 는 opaque text). 금액 `amount_krw` 양의 정수. 시각 RFC3339 UTC(`timestamptz`), 화면에서만 KST. 알 수 없는 필드 거부, `schema_version=1`. **enum 은 프론트 값이 표준**(D-21): 강도 `mild|spicy|hell`, 평결 `guilty|notGuilty|agree|disagree|dismissed`, 게시물 `spent|considering`, 형량 `probation|oneDay|life`, 카테고리 11종(`식비` `배달` `카페/간식` `교통/택시` `쇼핑/패션` `뷰티` `취미/여가` `술/유흥` `구독` `생활` `기타`). 짤 태그 5종(`GUILTY_HEAVY` 등)만 대문자
- 내부 API 는 snake_case, 공개 DTO 는 camelCase

## 2. DB

`ai` 스키마의 `jobs`(001)·준비·근거·메모리·원장(002·003)·`jobs.kind` 확장(006)은 AI 저장소 마이그레이션 러너가 만든다(§16.3). **004 는 백엔드 소유** — 업무 테이블 변경 + 백엔드가 쓰는 `ai` 테이블 3개.

| 테이블 | 필드·제약 |
|---|---|
| `posts` | `version int`, `audience_version int`, `intake_status`(`PASS`/`UNCLARIFIED`), `intake_source`(`AI`/`FALLBACK`), `submission_id UNIQUE`, `deleted_at`, `public_share_enabled bool DEFAULT false`. `item text NOT NULL`(≤30) · `reason` NULL 허용(≤200). 공유 방은 `post_rooms` |
| `votes` | `post_id, voter_id → profiles(id), room_id, verdict, reason(1~500)`. UNIQUE(`post_id, voter_id, room_id`) — 방마다 한 표 |
| `verdicts` | 방마다 하나(`post_id, room_id`). `verdict_version int`, `jury_result`, `policy_snapshot jsonb`(`allowed_sentences[{code,rank}]`, `fallback_sentence`, `reason_required`, `version`), `confirmed_at`, `deadline_at`, `sentence_status`(`PENDING`/`FINAL`), `sentence`, `sentence_source`(`AI`/`RULE`), `sentencing_reason`, `reason_source`(`AI`/`TEMPLATE`), `text_status`(`PENDING`/`GENERATING`/`TEMPLATE_READY`/`AI_READY`), `text_version bigint DEFAULT 0`, `active_generation_id uuid NULL`, `active_job_id uuid NULL`, `retry_round int DEFAULT 0`, `pending_retry_at`, `applied_intensity`, `meme_image_id` |
| `verdict_texts` | `verdict_id`, `intensity`, `headline`, `statement jsonb`(`{text, kind, evidence_labels}` 배열), `source`(`AI`/`TEMPLATE`), `text_version`, `dossier_id`; UNIQUE(`verdict_id, intensity`) |
| `submissions` | `id`, `actor_id`, `status`(`NEW`/`NEEDS_INPUT`/`COMPLETED`/`BLOCKED`/`EXPIRED`), `payload_hash`, `question_shown bool`, `final_check_count int`, `post_id`, `expires_at`(미사용, NULL), `intake_result jsonb` |
| `meme_images` | `tag`(5종), `strategies text[]`, `emotions text[]`(§11 6종), `keywords text[]`, `is_active`, `asset_key` |
| `ai.privacy_epochs` | `scope_key text PRIMARY KEY`(`user:{id}`·`room:{id}`·`post:{id}`), `epoch bigint NOT NULL DEFAULT 0`. **행이 없으면 epoch 0** |
| `ai.verdict_commit_records` | `generation_id uuid PRIMARY KEY`, `request_hash`, `verdict_id`, `text_version`, `committed_at` |
| `ai.text_evidence_refs` | `verdict_id`, `text_version`, `intensity`, `field_path`, `evidence_id → ai.evidence(id)` |

- `RETRYING` 상태는 없다. **재시도 중에도 `text_status=TEMPLATE_READY` 유지**
- 잠금 순서(전 코드 공유): **privacy scope 행(key 오름차순) → verdict 행 → job 행.** claim/heartbeat 는 job 행만. **LLM·내부 HTTP 를 DB 트랜잭션 안에서 기다리지 않는다**(intake 도 트랜잭션 밖)
- 002 `ai.evidence.fact_type` CHECK 는 `SPEND`·`VERDICT`·`MITIGATION`·`RULE`·`AGGREGATE` 5종

## 3. 업무 트랜잭션에서 `ai.jobs` INSERT

| 업무 트랜잭션 | kind / event_type | dedupe_key | priority | max_attempts | deadline_at | payload | aggregate_id / version |
|---|---|---|---:|---:|---|---|---|
| 게시물 저장(`spent`·`considering`) | `PREPARE` / `post.created` | `prepare:{post_id}:{post_version}:{audience_version}` | 30 | 2 | null | `{post_id, post_version, audience_version}` | `post_id` / `post_version` |
| 배심원 평결 확정(전원 투표 즉시 or 마감 스캔) — **D-24 게이트 뒤 INSERT**(§6) | `SENTENCE` / `verdict.confirmed` | `sentence:{verdict_id}:{verdict_version}` | 100 | 2 | INSERT 시각 + **90s** | `{verdict_id, verdict_version, post_id}` | `verdict_id` / `verdict_version` |
| 판결 최초 저장(finalize 또는 watchdog) | `RETAIN` / `sentence.finalized` | `retain:verdict:{verdict_id}:{version}` | 10 | 5 | null | `{event:"sentence.finalized", verdict_id, comment_id:null, version}` | `verdict_id` / `version` |
| 승인된 댓글 | `RETAIN` / `comment.approved` | `retain:comment:{comment_id}:{version}` | 10 | 5 | null | `{event:"comment.approved", verdict_id:null, comment_id, version}` | `comment_id` / `version` |
| 템플릿 저장 뒤 재시도 | `TEXT_RETRY` / `verdict.text_retry` | `text-retry:{verdict_id}:{verdict_version}:{round}` | 50 | 1 | INSERT 시각 + **90s** | `{verdict_id, verdict_version, round, intensities?}` — `intensities` 는 선택, 빈 배열 거부 | `verdict_id` / `verdict_version` |
| 게시물 저장 — 떼거지봇이 멤버인 방마다 | `JURY_VOTE` / `jury.vote_requested` | `jury-vote:{post_id}:{room_id}:{voter_id}` | 60 | 2 | null | `{post_id, post_version, room_id, voter_id}` | `post_id` / `post_version` — 조건·순서는 **19 §3** |

- **payload 는 kind 별 모양 그대로, 알 수 없는 필드는 워커가 거부한다**(AI 저장소 `src/geoji_ai/contracts/jobs.py`). RETAIN 은 네 키가 모두 있어야 하고 쓰지 않는 id 는 `null`. TEXT_RETRY `intensities` 는 빠지면 `target_intensities` 전체를 다시 쓰고, `target_intensities` 밖 강도가 있으면 워커가 모델 호출 없이 `generation-failed(SCHEMA_INVALID)`
- `payload` 에는 **참조(ID·version)만.** 사유·댓글을 복제하지 않는다
- `schema_version` 컬럼은 기본값 1(001 CHECK `= 1`). `kind` CHECK 는 001 4종 + 006 `JURY_VOTE`

```sql
INSERT INTO ai.jobs (id, event_id, event_type, kind, dedupe_key, aggregate_id, aggregate_version, schema_version, payload, priority, max_attempts, deadline_at, trace_id)
VALUES (gen_random_uuid(), gen_random_uuid(), 'verdict.confirmed', 'SENTENCE', 'sentence:' || :verdict_id || ':' || :verdict_version,
        :verdict_id, :verdict_version, 1, jsonb_build_object('verdict_id', :verdict_id, 'verdict_version', :verdict_version, 'post_id', :post_id),
        100, 2, now() + interval '90 seconds', :trace_id)
ON CONFLICT (dedupe_key) DO NOTHING;   -- 같은 업무 트랜잭션 안. commit 뒤 워커가 250ms 안에 집는다
```

- `dismissed`(정족수 미달 각하)는 **선고 작업을 만들지 않는다.** `disagree` 는 만든다 — 양형관만 건너뛴다
- 허용 목록이 비었거나 `fallback_sentence` 가 목록에 없으면 선고 작업을 만들지 않고 정책 설정 오류를 알린다. 임의 형량의 묵시적 기본값은 없다

## 4. 내부 API (서비스 인증 필수)

| API | 소유 | 절 |
|---|---|---|
| `POST /internal/v1/intake` | **AI API**(백엔드가 호출) | 아래 표 |
| `GET /internal/v1/ai-jobs/{job_id}/snapshot` | 백엔드 | §4.1 |
| `POST /internal/v1/ai-jobs/{job_id}/resolve-evidence` | 백엔드 | §4.2 |
| `POST /internal/v1/verdicts/{id}/begin-generation` | 백엔드 | §4.3 |
| `POST /internal/v1/verdicts/{id}/finalize` | 백엔드 | §5 |
| `POST /internal/v1/verdicts/{id}/generation-failed` | 백엔드 | §4.6 |
| `POST /internal/v1/posts/{post_id}/jury-votes` | 백엔드 | **19 §5** |
| `GET /internal/v1/trials/{post_id}/trace` · `GET /internal/v1/metrics/snapshot` | AI API(관측) | §16.2 |

**intake.** 요청 `{schema_version, submission_id, payload_hash, mode: INITIAL|FINAL_CHECK, post_type, amount_krw, category, item, reason}` → `IntakeResult{schema_version, mode, status: PASS|NEEDS_CLARIFICATION|BLOCKED, item_review{status, suggested_item}, message, category_review{status, suggested_category, confidence}, injection_detected, intake_source: AI|FALLBACK}`. 응답에 `submission_id`·`payload_hash` 에코는 없다. 백엔드 타임아웃 5초, 실패 = `FALLBACK` 등록 허용.

| 경우 | 응답 |
|---|---|
| 정상 | OpenAI `MODEL_JUDGMENT` 호출. `intake_source=AI`. 모델 timeout = `INTAKE_TIMEOUT_SECONDS`(기본 4초) − 0.2초 |
| 키 없음 · 벤더 실패 · timeout · 제출 예산 초과 | **200** `status=PASS`·`intake_source=FALLBACK`·`message=""`·`category_review{OK, null, 0.0}` |
| 강한 인젝션·무관 텍스트(코드 규칙) | 모델 호출 없이 `status=BLOCKED`·`intake_source=AI`·`message=null` |
| 필수값 위반(`item` 공백 제거 뒤 1~30자, `reason` ≤200, `amount_krw > 0`) | **422** `{"code": "ITEM_LENGTH"|"REASON_LENGTH"|"AMOUNT"}` |
| 스키마 위반(알 수 없는 필드·enum 밖·잘못된 JSON) | 422 `{"code": "INVALID_REQUEST"}` — 입력값은 본문에 싣지 않는다 |
| 인증 실패 | 401 `{"code": "UNAUTHORIZED"}` |

- `mode=FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 내지 않는다
- AI API 에 `DATABASE_URL` 이 있으면 intake 호출이 `ai.case_budgets`·`ai.llm_calls` 에 `post_id='submission:{submission_id}'` 로 기록된다. 제출당 cap 1,034 micro-USD. 초과 시 폴백 행

### 4.1 snapshot
- 검증: job 존재 ∧ `RUNNING` ∧ 헤더 `X-Generation-Id` 일치 ∧ lease 유효. 아니면 409 `STALE_GENERATION`. 원본이 삭제됐으면 404 `NOT_FOUND` — 워커는 job 을 `CANCELLED` 로 닫는다
- 응답 `CaseSnapshot`(정본 `contracts/case-snapshot-v1.schema.json`, **평면**): `schema_version, post_id, author_id, post_version, item, reason, amount_krw, category, post_type, created_at`, `audience{room_ids, audience_version, public_share_enabled}`, `privacy_versions[{scope_key, epoch}]`(post·author·**공유 방 전부**), `room_snapshots[{room_id, intensity, rule_version}]`, `intake_result`(없으면 null), `jury`(SENTENCE·TEXT_RETRY 만, 아니면 null: `verdict_id, verdict_version, result, vote_counts, guilty_ratio(0..1), confirmed_at, deadline_at, policy{version, allowed_sentences[{code, rank}], fallback_sentence, reason_required}, target_intensities, default_intensity`)
- 판결이 걸린 job(SENTENCE·TEXT_RETRY)의 `room_snapshots`·`audience.room_ids` 는 **그 판결 방 하나**, `privacy_versions` 는 공유 방 전부(finalize 3단계와 맞춘다). PREPARE·JURY_VOTE 는 공유 방 전부
- RETAIN 확장(선택 필드): `sentence.finalized` 면 `verdict_final{sentence, sentence_source, sentencing_reason, reason_source, applied_intensity, banter_strategy}` + `jury`, `comment.approved` 면 `comment{comment_id, version, room_id, post_id, post_status, author_id, content(≤1000), created_at}`. 확장 필드가 없으면 워커는 행 0 으로 complete(기억이 쌓이지 않는다)

### 4.2 resolve-evidence
- 요청 `{candidates[{source_type, source_id, source_version, score}], include: ["rules","aggregates","recent_verdicts","style_comments"]}`(후보 ≤20)
- **자유 조회 API 가 아니다.** job 이 가리키는 사건의 작성자·대상 방·공개 정책에 맞는 것만, 현재 권한·원본 버전으로 걸러 stale 후보는 제외
- 응답 `{sources[{source_type, source_id, source_version, payload, scope{visibility: PUBLIC|ROOMS|PRIVATE, room_ids[]}}], aggregates{burn_rate(0~1), tier, no_spend_days, repeat_same_category_30d, excludes_post_id, window{start_at, end_at}, rule_version}, room_rules[{room_id, rule_id, version, text}], recent_verdicts[{post_id, post_version, category, amount_krw, reason, result, sentence|null, judged_at, scope}], style_comments[]}` — 다섯 키 모두 필수, 알 수 없는 필드 거부. `style_comments` 는 P0 빈 배열
- 반복 집계: 현재 사건 제외, 사건 생성 시각 이전 30일, 같은 카테고리 확정 소비 건수. 항목 단위 숫자는 만들지 않는다
- `room_rules[].rule_id` 는 방 내부 인덱스라 RULE 후보는 워커 matcher 가 비활성이고 백엔드도 RULE sources 를 제외한다

### 4.3 begin-generation
- 요청 `{job_id, generation_id, verdict_version}`. §2 잠금 순서로 verdict·job 을 잠그고 lease·generation·평결 버전 확인 → `active_job_id`·`active_generation_id` 설정. 같은 generation 재호출 허용
- SENTENCE 는 **마감 전 `PENDING`** 에서만, TEXT_RETRY 는 **템플릿이 이미 노출된 `FINAL`** 에서만
- 응답 `{fixed_sentencing: {sentence, sentencing_reason, reason_source} | null, text_version, deadline_at}` — FINAL 이면 고정값(워커는 양형관을 부르지 않는다)
- 거부: `verdict_version` 불일치·이미 실패 보고한 generation·다른 활성 generation 유효·job 이 `RUNNING ∧ generation 일치 ∧ lease 유효` 가 아님·`AI_READY` 인데 TEXT_RETRY → 409 `STALE_GENERATION`. PENDING 인데 마감 지남 → 409 `DEADLINE_EXCEEDED`. verdict 없음 → 404

### 4.6 generation-failed · 오류 코드 표
- 요청 `{job_id, generation_id, error_code}`. 현재 세대만 처리, 다른 세대는 409 `STALE_GENERATION`. **같은 세대·같은 코드 재전송은 200**

| error_code | 백엔드 처리 |
|---|---|
| `AI_NOT_READY` · `POLICY_ERROR` · `EVIDENCE_INVALIDATED` | 즉시 폴백(형량 `fallback_sentence` FINAL/RULE, 문구 TEMPLATE, RETAIN job). **TEXT_RETRY 예약 안 함** |
| `VENDOR_UNAVAILABLE` · `BUDGET_EXCEEDED` · `EVAL_FAILED` · `SCHEMA_INVALID` · `DEADLINE_EXCEEDED` | 같은 폴백 + `TEXT_RETRY` round 1 예약(§7) |
| TEXT_RETRY 중 실패 | 템플릿 유지, 다음 round 예약(남았으면), 마지막이면 운영 알림 |

- 서기가 AI 문구를 하나도 못 냈을 때 코드: 예산 초과 → `BUDGET_EXCEEDED`, 시간 초과 → `DEADLINE_EXCEEDED`, 그 밖 → `VENDOR_UNAVAILABLE`
- **TEXT_RETRY round 안의 실패는 저장하지 않는다.** TEMPLATE finalize 없이 `generation-failed` 만

### 4.7 공통 규약 — 인증 · 헤더 · 타임아웃 · 거부 본문
- **인증은 양방향.** `Authorization: Bearer <SERVICE_AUTH_TOKEN>`(같은 값). AI API 는 `/health/*` 만 무인증, 토큰 설정값이 비어 있으면 전부 401. 비교는 timing-safe
- 워커 → 백엔드 헤더 5종: `Authorization`, `X-Trace-Id`(job `trace_id`), `X-Request-Id`(**시도마다** 새 uuid4), `X-Job-Id`, `X-Generation-Id`. 본문이 있으면 `Content-Type: application/json`
- 워커 타임아웃: connect 0.5초 공통, read 는 snapshot 2 · resolve-evidence 2 · begin-generation 1 · finalize 3 · generation-failed 1 · jury-votes 2초
- 워커 재전송: transport 오류·timeout·5xx 에 **같은 본문 바이트**를 최대 2회(200ms·600ms 뒤). 그래도 실패면 job `BACKEND_UNAVAILABLE`, 5초 뒤 재시도. **4xx 는 재전송하지 않는다** — 백엔드는 같은 요청 재도착을 멱등하게 받는다
- **거부 응답 본문은 양쪽 모두 `{"code": "<코드>"}`.** 워커는 `code` 를 읽고 없으면 `HTTP_<status>`. 코드 이름: 401 `UNAUTHORIZED` · 404 `NOT_FOUND` · 409 `STALE_GENERATION`·`IDEMPOTENCY_CONFLICT`·`EVIDENCE_INVALIDATED`·`DEADLINE_EXCEEDED`·`VOTING_CLOSED`·`ALREADY_VOTED` · 403 `NOT_AI_JUROR` · 422 `INVALID_REQUEST`·`INVALID_DRAFT`
- AI API 가 내는 거부: 401 `UNAUTHORIZED`(`WWW-Authenticate: Bearer`), intake 422 `ITEM_LENGTH`·`REASON_LENGTH`·`AMOUNT`, 스키마 위반 422 `INVALID_REQUEST`, trace 404 `TRACE_NOT_FOUND`, `DATABASE_URL` 없음 503 `DB_UNAVAILABLE`. `/health/ready` 503 은 상태 보고라 본문이 다르다(§16.2)

## 5. finalize

**카드 규격(9/17):** 제목 1~20자, `statement` 정확히 1항목, 본문 1~100자(9/19), 줄바꿈·공백만 있는 문구 거부. 길이는 공백·문장부호 포함 Unicode code point 수. 기존 저장 문구를 읽는 DTO 는 제목 30자·본문 최대 4항목·합계 300자 상한 유지. 백엔드 파서는 본문을 `array(statementNode, 1, 4)` 로 받는다.

요청 `FinalizeRequest`(정본 `contracts/finalize-v1.schema.json`): `schema_version`·`job_id`·`generation_id`·`verdict_version`·`expected_text_version`·`dossier_id`·`privacy_versions`·`draft_hash`·`sentencing{sentence, sentencing_reason, reason_source, evidence_labels, aggravating, mitigating} | null`·`draft{texts[{intensity, headline, statement, banter_strategy, selected_candidate_id, attack_angle, source}], meme_tag, meme_hints{emotion, keywords}}`·`evaluation`·`evaluation_draft_hash`·`prompt_bundle_version`·`guardrail_policy_version`·`model_ids{sentencing, writer, evaluator}`. 전부 필수(`sentencing` 만 null 가능), 알 수 없는 필드 거부.

- `sentencing.reason_source` 와 `texts[].source` 는 `AI | TEMPLATE`
- `meme_hints.emotion` 은 6종 enum `DISAPPROVAL`·`ABSURD_SERIOUSNESS`·`SMUG`·`PITY`·`CELEBRATION`·`RESIGNATION`. `keywords` ≤10개·각 ≤30자
- `guardrail_policy_version` 은 `FinalizeRequestParser.GUARDRAIL_VERSIONS`(`guardrail-v1`·`guardrail-v2`) 안이어야 한다. **AI 가 정책 버전을 올리면 백엔드 허용 목록을 먼저 늘려야 한다**(아니면 422 로 판결이 전부 막힌다)

**`draft_hash` 규칙**(AI 저장소 `src/geoji_ai/domain/draft_hash.py`, `evaluation_draft_hash` 도 같다. 백엔드가 다시 계산해 비교):
1. `{"draft": <WriterDraft>, "sentencing": <SentencingDecision 또는 null>}`
2. 모든 문자열(키·값) 유니코드 NFC. 배열 순서 유지
3. 키 코드포인트 순 정렬, 구분자 `,` `:`(공백 없음), 비 ASCII 이스케이프 없음 JSON
4. UTF-8 sha256 **소문자 hex**

**백엔드는 호출자가 보낸 평결·default intensity·허용 목록·`sentence_source` 를 신뢰하지 않는다. DB 에서 읽는다.** 문구·검수 hash 일치, 근거 라벨→UUID(`ai.dossiers.label_map`), 길이, 강도 집합, 정책 버전 같은 결정적 검증을 서버에서 다시 한다. 의미 검수는 AI 책임이지만 보고서 누락·`false` 는 백엔드가 거부한다.

```text
BEGIN
  1. 같은 generation 의 commit record 가 있으면: request_hash 일치 → 이전 성공 응답 / 불일치 → 409 IDEMPOTENCY_CONFLICT
  2. privacy scope(key 오름차순) → verdict → job 순으로 잠금
  3. commit record 재확인. 원본 삭제 여부·현재 privacy epoch(공유 방 전부의 room: 키 포함)·audience version 재확인
  4. job RUNNING ∧ lease 유효 ∧ generation 일치
  5. verdict_version · active_job_id · active_generation_id · expected_text_version 일치
  6. 최초 SENTENCE 는 DB now() < deadline_at. TEXT_RETRY 는 job 자체 제한 시각
  7. 허용 목록·결과·출력 구조·근거 라벨→UUID 매핑·검수 대상 hash 검사
  8. sentence PENDING 이면 검증된 후보로 FINAL 한 번만 갱신(sentence_source=AI). FINAL 이면 입력 후보가 기존 형량과 같아야 함.
     sentencing_reason 은 FINAL 이후 변경 불가 — reason_source=TEMPLATE 치환만 허용(D-19)
  9. texts 를 새 text_version 으로 원자적 저장(verdict_texts, source 포함). text_evidence_refs 도 같은 version
 10. text_status = AI_READY(모든 강도 AI) / TEMPLATE_READY 유지 + 재시도 대상(일부 강도 TEMPLATE, TEXT_RETRY payload intensities[] = TEMPLATE 강도).
     TEXT_RETRY 의 texts 는 중복 없음 ∧ ⊆ target_intensities 면 통과, 받은 강도 행만 갱신. 짤 선택(§11) → meme_image_id 고정. active generation 해제
 11. 최초 형량 확정 때만 RETAIN job INSERT ... ON CONFLICT DO NOTHING
 12. job SUCCEEDED, commit record INSERT
COMMIT
```

| 결과 | HTTP |
|---|---|
| 저장 성공 · 동일 성공 재전송 | 200 `{verdict_id, text_version, committed_at}` |
| 다른 generation 활성 | 409 `STALE_GENERATION` — 워커는 결과 폐기 |
| 같은 generation 다른 본문 | 409 `IDEMPOTENCY_CONFLICT` |
| Evidence 삭제·공개 범위 변경·epoch 불일치 | 409 `EVIDENCE_INVALIDATED` — 초안 폐기 |
| 최초 노출 마감 초과 | 409 `DEADLINE_EXCEEDED` — watchdog 폴백 |
| 미확정 형량·schema·검수 오류·정책 버전 | 422 `INVALID_DRAFT` |
| DB·네트워크 장애 | 워커가 같은 요청 재전송(§4.7) → commit record. **모델 재호출 없음** |

- 첫 성공 후 삭제가 일어났어도 commit record 는 **commit 사실만** 돌려준다. 최종 문구는 권한 검증된 GET 으로
- 비유죄(`notGuilty`·`agree`·`disagree`) 는 `sentencing: null` 로 finalize 한다

## 6. deadline watchdog · D-24 SENTENCE 게이트

**게이트.** 평결 확정 시점에 같은 `post_id` 의 PREPARE job 이 `QUEUED`·`RUNNING` 이면 SENTENCE 를 바로 넣지 않는다. 스케줄러(250ms)가 **PREPARE 종료(`SUCCEEDED`·`FAILED`·`CANCELLED`) 또는 `confirmed_at + 30s`** 에 INSERT 하고 job·verdict `deadline_at` 을 INSERT 시각 + 90s 로 둔다. 기다리는 동안 `sentence_status=PENDING`·`text_status=PENDING`, 공개 API 는 `view=null`. PREPARE job 이 없으면 바로 INSERT.

**watchdog.** 250ms 주기로 마감 초과 `PENDING` 판결을 찾는다. 여러 인스턴스여도 §2 잠금 + 조건부 갱신으로 한 번만.
1. privacy scope · verdict · active job 을 같은 순서로 잠근다
2. 이미 `AI_READY` 또는 `FINAL + TEMPLATE_READY` 면 반복하지 않는다
3. 미확정 형량을 `policy_snapshot.fallback_sentence` 로 `FINAL`, `sentence_source=RULE`, 이유·문구는 `TEMPLATE`(§10)
4. `active_generation_id` 비우고 이전 job `CANCELLED`(늦게 끝난 워커의 complete 는 0행)
5. `RETAIN` 과 `TEXT_RETRY` round 1 을 같은 트랜잭션에 기록
6. 이전 워커 응답은 generation/상태 불일치로 거부

- 체감 지연 = PREPARE 대기(≤30초) + 90초 상한. 검수가 1회에 통과하면 40초대, 재작성까지 가면 60초대. 90 은 상한이지 대기 시간이 아니다

## 7. 재시도 round · reaper

- round 1 은 템플릿 후 5분, round 2 는 10분, round 3 은 20분 뒤 `TEXT_RETRY` INSERT(`retry_round <= 3`, `(verdict_id, verdict_version, round)` 중복 방지). 스캔 주기 5분
- 최초 저장 형량·양형 이유 고정. 문구만 갱신. 마지막 실패·비용 한도 초과는 템플릿 유지 + 운영 알림
- reaper(5초, 백엔드 스케줄러): lease 만료 회수. 원문은 AI 저장소 `src/geoji_ai/adapters/postgres_jobs.py` `REAPER_SQL`

```sql
UPDATE ai.jobs SET status = CASE
    WHEN kind = 'TEXT_RETRY' THEN 'FAILED'
    WHEN kind = 'SENTENCE' AND (deadline_at IS NULL OR deadline_at <= now()) THEN 'CANCELLED'
    WHEN attempts >= max_attempts THEN 'FAILED'
    ELSE 'QUEUED' END,
  owner_id = NULL, generation_id = NULL, lease_until = NULL,
  last_error_code = COALESCE(last_error_code, 'LEASE_EXPIRED'), updated_at = now()
WHERE status = 'RUNNING' AND lease_until < now();
```
- 운영에서 워커의 `--reaper` 는 끈다(로컬 전용)

## 8. 삭제 · 권한 변경 (privacy epoch · D-26)

- 원본 삭제·댓글 삭제·작성자 탈퇴·방 공유 철회: **해당 scope 의 `ai.privacy_epochs` 를 먼저 잠그고 증가**, 같은 트랜잭션에서 원본 비활성화, 무효화 기록. 판결 생성은 이전 epoch 로 저장할 수 없다(finalize 3단계)
- **진행 중 작업 끄기(D-26):** 같은 무효화 트랜잭션에서 영향받는 게시물의 `QUEUED`·`RUNNING` `PREPARE`·`SENTENCE`·`TEXT_RETRY`·`JURY_VOTE` 를 `CANCELLED`(`owner_id`·`generation_id`·`lease_until` NULL). TEXT_RETRY payload 에는 `post_id` 가 없어 `verdict_id` → post 로 찾는다. 워커는 다음 heartbeat(5초) 안에 멈추고, 모델 호출 직전마다 epoch 를 확인한다
- **파생 정리는 비동기여도 읽기 차단은 즉시.** 판결·share-card 읽기는 현재 `verdict_id/intensity/text_version` 의 refs 를 따라 POST·VERDICT·COMMENT 원천 삭제를 확인하고 즉시 템플릿으로 바꾼다(같은 `textVersion` 의 `view.source=TEMPLATE` 응답을 UI 가 반영한다)
- 무효화 SQL: AI 저장소 `database/sql/invalidate_scope.sql`(백엔드로 복사, §16.3). **한 트랜잭션**, 바인드 `:t`(source_type)·`:id`(source_id)·`:scope_key`. POST 무효화는 직접 source 일치 외에 `memory_facts.payload.post_id` 가 같은 VERDICT·COMMENT·RULE_HIT 도 soft-delete 한다. `text_evidence_refs` → 템플릿 전환은 백엔드가 쓴다. **이미 `FINAL` 인 형량은 유지**

## 9. 공개 API

| API | 요청·응답 | 오류 |
|---|---|---|
| `POST /api/post-submissions` | `{postType,amountKrw,category,item,reason,roomIds}` → `{submissionId,status,revision,intakeResult,postId}`. 내부 `/internal/v1/intake(mode=INITIAL)` 은 트랜잭션 밖 | 400 입력, 401, 403 공유 권한 |
| `POST /api/post-submissions/{id}/complete` | `{action: REVISE|PROCEED, 최종 값, revision}`. `REVISE` 는 `FINAL_CHECK` 1회. **`BLOCKED` 를 `PROCEED` 로 우회 불가(409).** 중복 완료는 기존 post 반환 | 409 버전 충돌·차단 |
| `POST /api/posts/{postId}/votes` | `{verdict, reason(1~500), roomId}`. 방마다 한 표, 작성자 제외. 전원 투표면 같은 요청에서 평결 확정 | 403·400·409 |
| `GET /api/posts/{id}/verdict?room_id=` | `verdict-view-v1`(camelCase): `schemaVersion, postId, juryStatus|null, sentenceStatus, textStatus, textVersion, view|null, pollAfterMs`. 생성 중 `view=null` | 404 없음/권한 없음/삭제 |
| `GET /api/posts/{id}/share-card?room_id=` | 공개 허용 문구·이미지 metadata 만. **Evidence 원문·개인 이력 반환 금지** | 404 |
| `POST /api/rooms/{roomId}/ai-member` | 떼거지봇 멤버 추가 + 템플릿 글 2개 — **19 §6** | 404·503 |

- **재판은 방마다 따로**(9/16). 정족수는 그 방 멤버만, 두 방에 다 있는 사람은 방마다 한 표. 정족수 min(2, 가능 인원)
- **전달은 폴링만.** Realtime·SSE 없음. `textVersion` 이 작은 응답으로 UI 를 덮지 않는다
- `source=TEMPLATE` 이면 AI 판사 라벨·양형 이유 블록 숨김. 지옥맛 방장 확인 문구: "지옥맛은 반말과 욕설, 인격 조롱이 나옵니다. 멤버 전원이 동의했는지 확인해주세요."

## 10. 템플릿 공유 파일

`contracts/fixtures/templates-v1.json`(AI 저장소, 백엔드에 복사·버전 고정). `{version: "templates-v1", sentence_labels{probation, oneDay, life}, results{guilty|notGuilty|agree|disagree: {headline, statement[], sentencing_reason_template}}}`. 네 결과 모두 §5 카드 규격의 본문 1항목. `sentencing_reason_template` 은 유죄만 `"형량: {sentence_label}"`, 나머지 null. watchdog·generation-failed·부분 강도 템플릿에 쓴다.

## 11. 짤 점수 선택 (finalize 10단계)

`meme_images` 중 `tag == draft.meme_tag ∧ is_active` → `+3` `banter_strategy ∈ strategies` · `+2` `meme_hints.emotion ∈ emotions` · `+1` `|keywords ∩ meme_hints.keywords|` · `−5` 같은 사용자 최근 노출 5장 → `crc32(post_id + image_id)` tie-break → `meme_image_id` 고정. 후보 0 → 결과별 기본 이미지. 감정 6종은 §5 enum. 문구 합성은 클라이언트 캔버스.

## 12. 시드

백엔드 `--spring.profiles.active=seed` + `GEOJI_SEED_USER_IDS`(auth 사용자 4개) 로 방 3·게시물 12·댓글 20·판결 10 을 실제 파이프라인으로 만든다. AI 시드(`scripts/seed_agent_db.py`, §16.3)는 그 뒤에 데모 C 사용자·방·스타벅스 post 2개 id 를 받아 돈다. `meme_catalog` 은 만들지 않는다.

## 13. 백엔드 수용 검사

| 케이스 | 기대 |
|---|---|
| 마지막 표 동시 도착 | 평결 커밋 1회, SENTENCE job 1개(dedupe), 형량 FINAL 1회 |
| 평결 commit 직후 백엔드 프로세스 중단 | job 은 커밋돼 있고 워커가 처리. watchdog 이 중복 확정하지 않음 |
| finalize 응답 유실 후 재전송 | commit record 로 같은 200, `text_version` 동일 |
| 중복 완료 요청 | 기존 post 반환, PREPARE job 1개 |
| `PROCEED` 로 `BLOCKED` 우회 | 409 |
| 검토 후 사유 교체 | `REVISE` 가 `FINAL_CHECK` 를 거쳐 `BLOCKED` 가능 |
| 질문 두 번 노출 | `question_shown` 로 차단 |
| 템플릿 이후 늦은 성공 | 409 `STALE_GENERATION` |
| retry 중 삭제 | epoch 불일치 → 409 `EVIDENCE_INVALIDATED`, 조회 즉시 템플릿 |
| 방 2개 이상 공유 글의 finalize | `privacy_versions` 에 방 전부의 키 → 200(9/19 server PR #39) |
| 떼거지봇 | 19 §9 |

## 16. 배포 · 연동

### 16.1 배포 환경변수 (EC2 compose)

AI `Settings` 는 프로세스 환경변수를 `.env` 보다 우선한다. 키를 바꾸면 `ai-api`·`ai-worker` 둘 다 재시작. 둘 다 같은 목록을 받는다. 키 이름 전체는 AI 저장소 `.env.example`, 기본값은 `README.md` 설정 표. 여기는 **직접 적어야 하는 것**만.

| 키 | 값 | 없으면 |
|---|---|---|
| `APP_ENV` | `production` | 개발 모드(기동 검사 느슨) |
| `GUARDRAIL_POLICY_VERSION` | `guardrail-v2` | **기동 실패.** production 은 환경에 직접 적어야 한다 |
| `OPENAI_API_KEY` · `XAI_API_KEY` | AI 파트가 전달 | `/health/ready` 503, intake 폴백 |
| `DATABASE_URL` | Supabase Session Pooler(`ai_api`/`ai_worker` role). `postgresql://` 그대로 줘도 된다 | `/health/ready` 503 |
| `BACKEND_INTERNAL_URL` | 백엔드 호스트 루트(예: `http://127.0.0.1:18080`), `/internal/v1` 을 붙이지 않는다 | **워커 기동 실패** |
| `SERVICE_AUTH_TOKEN` | 내부 API 서비스 토큰(양쪽 같은 값) | 전부 401 |
| `ALERT_DISCORD_WEBHOOK_URL` | 팀 디스코드 웹훅 | 알림 없음 |

- 시간 예산 키(`INTAKE_TIMEOUT_SECONDS` 4 · `SENTENCING_NODE_TIMEOUT_SECONDS` 12 · `WRITER_NODE_TIMEOUT_SECONDS` 10 · `EVALUATOR_NODE_TIMEOUT_SECONDS` 30 · `TEXT_RETRY_TIMEOUT_SECONDS` 60 · `JUROR_TIMEOUT_SECONDS` 10)는 **운영에서 적지 않는다.** 기본값보다 낮추면 검수관이 TIMEOUT 나 매 판결이 템플릿이 된다
- `WORKER_SLOTS` 기본 `{"SENTENCE":2,"PREPARE":1,"BACKGROUND":1,"JURY":1}`. `.env` 에 직접 적었다면 `JURY` 를 더한다(19 §8)
- `MODEL_WRITER` 에 추론 모델(`grok-4.6`·`4.5`·`4.3`)을 넣으면 기동 실패

### 16.2 AI API 엔드포인트

| 경로 | 응답 | 용도 | 인증 |
|---|---|---|---|
| `GET /health/live` | 200 `{"status":"ok"}` | 프로세스 생존. compose healthcheck | 무인증 |
| `GET /health/ready` | 200 `{"status":"ok","missing":[]}` 또는 503 `{"status":"not_ready","missing":[…]}` · 503 `{"status":"not_ready","db":"unreachable"}` | 키·DB 준비. 503 이면 트래픽을 보내지 않는다 | 무인증 |
| `POST /internal/v1/intake` | `IntakeResult` | 심문관(§4) | 서비스 |
| `GET /internal/v1/metrics/snapshot` | `{generated_at, db, metrics[]}` | 운영 지표 | 서비스 |
| `GET /internal/v1/trials/{post_id}/trace` | 라벨·코드·개수·시각(원문 없음). 없으면 404 `TRACE_NOT_FOUND` | 데모 C trace 화면. 백엔드 `TraceProxyController` 가 프록시 | 서비스 |

- 워커 프로세스에는 HTTP 엔드포인트가 없다. 로컬 기동은 README "실행 절차"

### 16.3 복사해 가거나 실행할 파일

| 파일·명령 | 어디에 | 왜 |
|---|---|---|
| `database/migrations/001·002·003·006` | 적용만 | `DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate`. 번호 순·`ai.schema_migrations` 기록·재적용 no-op·advisory lock. 004 는 백엔드 소유(러너가 번호 4 를 건너뜀), 005 는 P1. 006 은 `jobs.kind` CHECK 에 `JURY_VOTE`(19 §8) |
| `database/sql/invalidate_scope.sql` | 백엔드 무효화 스케줄러 | §8 |
| `src/geoji_ai/adapters/postgres_jobs.py` 의 `REAPER_SQL` | 백엔드 스케줄러 5초 | §7 |
| `contracts/fixtures/templates-v1.json` | 백엔드 저장소, 버전 고정 | §10 |
| `contracts/*-v1.schema.json` 7종 | 참조 | 계약 정본(`case-snapshot`·`evaluation`·`finalize`·`intake`·`sentencing`·`verdict-view`·`writer-draft`) |
| `geoji-ai ledger-sweep --older-than 24h` | 백엔드 스케줄러 또는 cron **하루 1회** | 결과 불명(`UNKNOWN`)·오래된 `RESERVED` 호출의 예약액 확정. `DATABASE_URL` 필요 |
| `scripts/seed_agent_db.py` · `scripts/seed_memory_demo_c.py` | 실행만(AI 저장소) | §12 데모 시드. 멱등 |
| `docs/runbook.md` | 참조 | 헬스·알림·비용 경고·롤백·동결·토큰 회전 |
| `ai-api`·`ai-worker` 이미지 · `docker-compose.prod.yml` | EC2 | 빌드 `docker build --platform linux/amd64 -t geoji-ai:ai-YYYYMMDD-N .`, 전달 `docker save geoji-ai:<태그> \| gzip > geoji-ai-<태그>.tar.gz`. EC2 에서 `docker load` → `.env` 의 `GEOJI_AI_IMAGE` → `docker compose -f docker-compose.prod.yml up -d`. 배포된 코드 대조는 `sentence_summary` 로그의 `prompt_bundle_version`. **최신 태그와 대조값은 그 태그를 전달한 문서(19 §8)에 적는다.** 9/19 운영 이미지 `ai-20260919-2` = `bundle-afa59044c8df` |
