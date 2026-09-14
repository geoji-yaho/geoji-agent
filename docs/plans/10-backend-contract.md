# 🤝 [Contract] 백엔드 계약서 — 공유 Postgres · `ai.jobs` INSERT · 내부 API 5개 · finalize · watchdog · 재시도 · 삭제 epoch · 공개 API · 시드 · 배포·연동

> 근거: proposal2 §1(아키텍처), §2.2 결정 16~24, §7.1 업무 테이블 추가 필드, §8.1 이벤트·큐, §9 API 계약, §10 판결 확정 트랜잭션, §11 마감·재시도·삭제 경합, §12(정책 버전이 finalize 에 걸림), §17 짤, §19 role 분리, §20 작업 3·8 완료 기준·먼저 실패시킬 케이스, §21 백엔드·프론트 변경 계약. proposal2 는 git 에 없으므로 백엔드가 **이 파일 한 장만 읽으면** 되게 옮겨 적었다.
> **대상 독자: 백엔드 담당. 9/14 부터 백엔드가 읽는 문서는 이 한 장이다.** 계약(§1~§12)·배포와 연동(§16)·백엔드가 할 일과 전달 상태(§0.1)·회신 대기(§14)가 모두 여기 있다. AI 파트 계획서(01~09)는 이 문서를 참조한다. 계약이 바뀌면 여기와 `01-contracts-fake-provider.md` 를 같이 고친다.
> **9/14 코드 대조:** AI 저장소 `main`(52b6433) 코드와 어긋나던 문장을 코드 기준으로 고쳤다. 고친 곳은 `(9/14 코드 대조)`, 크게 바뀐 옛 문장은 `(원문)` 으로 남겼다. 코드에 아직 없는 백엔드 요구(`(제안)`, D-24·D-26)는 그대로 백엔드 몫이다.
> **9/8 갱신: D-20 · D-21 · 전달 방식 · 배포는 §15 에서 확정됐다. 서버에는 재판 흐름이 아직 없으므로 §2~§9 가 전부 신규 작업이다.** (원문) **가장 먼저 답해야 할 것: D-20.** 백엔드와 AI 가 같은 Postgres 인스턴스를 쓰고, 업무 트랜잭션 안에서 `ai.jobs` 를 INSERT 할 수 있는가. 아니면 outbox 전달 계층이 먼저 필요하고 9/8~9/18 일정 전체가 흔들린다(proposal2 부록 D). **9/8 까지 회신.**

## 0. 읽는 법

| 절 | 내용 | 필요 시점 |
|---|---|---|
| §0.1 | **백엔드가 할 일 체크리스트** — 무엇이 바뀌었고 무엇을 해야 하는지, 전달 상태 | 늘 |
| §1 | 전제·아키텍처·role·grants | 9/8 |
| §2 | DB — `ai` 스키마 권한, 마이그레이션 `004`(업무 테이블 변경) | 9/10 |
| §3 | 업무 트랜잭션에서 `ai.jobs` INSERT 5지점 | 9/10(2지점)·9/13(3지점) |
| §4 | 내부 API — intake · snapshot · resolve-evidence · begin-generation · generation-failed(+오류 코드) · (제안) 매핑·trace · §4.7 공통 규약(인증·헤더·타임아웃·거부 본문) | 9/10~9/13 |
| §5 | finalize 절차 12단계·결과 코드·`draft_hash` 규칙 | 9/13 |
| §6 | deadline watchdog | 9/10 |
| §7 | 재시도 round 스케줄러 · reaper | 9/13 |
| §8 | 삭제·권한 변경 — privacy epoch·무효화 | 9/12 |
| §9 | 공개 API — post-submissions · complete · verdict 폴링 · share-card | 9/10·9/14 |
| §10 | 템플릿 공유 파일 | 9/10 |
| §11 | 짤 점수 선택(finalize) | 9/17 |
| §12 | 시드 | 9/17 |
| §13 | 백엔드 수용 검사(먼저 실패시킬 케이스) | 각 시점 |
| §14 | 미결(백엔드 회신 대기) | — |
| §15 | 날짜별 결정 기록 | — |
| §16 | **배포·연동** — 환경변수 · AI API 엔드포인트 · 복사해 가거나 실행할 파일 | 배포 전 |

- 절 번호는 인용 ID 다(`10 §4.6`). 새 절은 번호를 밀지 않고 `§0.1`·`§4.7`·`§16` 처럼 붙인다
- 값의 정본: 환경변수 키 이름은 AI 저장소 `.env.example`, 기본값·설명은 AI 저장소 `README.md` 설정 표, JSON 모양은 `contracts/*-v1.schema.json`. 이 문서는 "무엇을 어떻게 붙이는가" 를 적는다

### 0.1 백엔드가 할 일 체크리스트

위가 최신이다. 상태는 `미전달` → `전달 9/NN`(백엔드에 보냄) → `반영`(백엔드 구현됨). 상태를 바꾸는 것은 사용자다. 9/14 이전 줄은 옛 백엔드 전달 파일 §4(9/14 삭제, 이 표로 흡수)에서 옮겼다.

| 날짜 | 무엇이 바뀌었나 → 백엔드가 할 일 | 절 | 상태 |
|---|---|---|---|
| 9/14 | D-26 무효화 트랜잭션에서 진행 중 job 끄기를 가짜 백엔드로 재현함 → 게시물 삭제·공유 철회 트랜잭션에서 epoch +1 먼저, 같은 트랜잭션에서 그 post 의 `QUEUED`·`RUNNING` PREPARE·SENTENCE·TEXT_RETRY 를 `CANCELLED`(`owner_id`·`generation_id`·`lease_until` NULL). **TEXT_RETRY payload 에는 `post_id` 가 없어 `verdict_id` → post 로 찾아야 한다.** 워커는 다음 heartbeat(기본 5초) 안에 멈추고 그 사이 나간 모델 호출 1건은 원장 `UNKNOWN` | §8 | 미전달 |
| 9/14 | D-24 SENTENCE 게이트를 가짜 백엔드로 재현함 → 평결 확정 때 같은 post(`payload->>'post_id'`) PREPARE 가 `QUEUED`·`RUNNING` 이면 보류, PREPARE 종료(`SUCCEEDED`·`FAILED`·`CANCELLED`) 또는 `confirmed_at + 30s` 에 INSERT, job·verdict `deadline_at` = INSERT 시각(DB `now()`) + 10s. 보류 중엔 마감이 없어 watchdog 대상 아님 | §3·§6 | 미전달 |
| 9/14 | AI API 거부 본문 `{"code"}` 로 변경(옛 `{"detail": {"code"}}`), 스키마 위반은 422 `INVALID_REQUEST` → intake·trace 호출부가 `code` 를 최상위에서 읽는다 | §4·§4.7·§15.5 | 미전달 |
| 9/14 | 계약 문서를 이 한 장으로 통일 → 옛 전달 파일 대신 이 문서만 본다 | §0 | 미전달 |
| 9/14 | D-24 SENTENCE 게이트 `(제안)` → PREPARE 종료 또는 확정 + 30초까지 INSERT 대기, 마감은 INSERT + 10초 | §3·§6·§14 | 미전달 |
| 9/14 | D-26 진행 중 작업 끄기 `(제안)` → 무효화 트랜잭션에서 영향 게시물의 진행 중 job `CANCELLED` | §8·§14 | 미전달 |
| 9/14 | `ledger-sweep` 가 오래된 `RESERVED` 도 정리 → 하루 1회 실행만 걸면 된다(설정 변경 없음) | §16.3 | 미전달 |
| 9/14 | 서기가 전부 시간 초과면 `generation-failed` 코드가 `DEADLINE_EXCEEDED` → 분기는 같고(round 예약) 집계 분포만 바뀐다 | §4.6 | 미전달 |
| 9/14 | 노드 timeout 환경변수가 소수를 받는다 → 할 일 없음(기본값 불변) | §16.1 | 미전달 |
| 9/14 | watchdog 뒤 이전 job 상태 → `CANCELLED` 인지 `complete` 인지 회신 | §6·§14 | 미전달 |
| 9/14 | finalize `meme_hints.emotion` 이 6종 enum → 스키마 복사본 갱신, `meme_images.emotions` 값 맞춤 | §5·§11 | 미전달 |
| 9/14 | `lease_expired_total` 은 워커가 못 본다 → 운영 reaper 가 회수 건수를 kind 별로 남겨 달라 | §7·§14 | 미전달 |
| 9/14 | finalize 422 가 `INVALID_DRAFT` 하나 → 형량 규칙 위반 전용 코드 둘 수 있는지 회신 | §5·§14 | 미전달 |
| 9/14 | TEXT_RETRY 강도 집합 → finalize 검증을 `⊆ target_intensities` 로, payload 에 `intensities[]` | §3·§5·§14 | 미전달 |
| 9/14 | TEXT_RETRY round 안 실패는 저장 없이 `generation-failed` 만 → 다음 round 예약은 백엔드 | §4.6·§7 | 미전달 |
| 9/14 | `ai.llm_calls` 정리 표시 → 집계 시 `status='UNKNOWN' ∧ actual_micro_usd NOT NULL` = 정리됨 | §16.3 | 미전달 |
| 9/14 | intake 가 실제 모델을 부른다(스텁 끝) → 폴백·`BLOCKED`·422 규칙대로 호출부 처리 | §4 | 미전달 |
| 9/14 | 제출 임시 예산 → intake 호출이 `submission:{id}` 키로 원장 기록. 매핑 통지(§4.4) 회신 | §4.4·§14 | 미전달 |
| 9/14 | intake 응답에 `submission_id`·`payload_hash` 에코 없음 → 요청·응답 묶기는 호출 쪽에서 | §4 | 미전달 |
| 9/14 | 002 `ai.evidence.fact_type` CHECK 에 `REASON_ANALYSIS` 없음 → AI 파트 결정 뒤 넓히면 마이그레이션 재적용만(백엔드 작업 없음) | §2 | 미전달 |
| 9/14 | `draft_hash` canonical 규칙 → finalize 가 같은 규칙으로 재계산 | §5 | 미전달 |
| 9/14 | RETAIN snapshot 확장 필드 없으면 기억 0 → `verdict_final`·`jury`·`comment` 를 채워 달라, 댓글 방은 `room_snapshots` 에 | §4.1 | 미전달 |
| 9/14 | RETAIN 원본 삭제 시 snapshot **404** → 409 등은 워커가 재시도한다 | §4.1 | 미전달 |
| 9/14 | 002·003 grants 확정 → role 을 만든 뒤 마이그레이션 적용 | §1·§16.3 | 미전달 |
| 9/14 | `privacy_epochs` 정의·권한 가정 → 004 초안이 다르면 회신 | §2·§14 | 미전달 |
| 9/14 | `generation-failed` 오류 코드 표를 가짜 백엔드가 그대로 구현 → 채택 회신 | §4.6·§14 | 미전달 |
| 9/14 | 내부 API 5종 상태 기계(가짜 백엔드 기준) → 실제 구현이 다르면 회신 | §4.3·§4.6·§5·§14 | 미전달 |
| 9/14 | 거부 응답 본문 `{"code"}` → 401·404·422 코드 이름 확정 회신 | §4.7·§14 | 미전달 |
| 9/14 | 양방향 서비스 인증 → `/health/*` 만 무인증 | §4.7 | 미전달 |
| 9/14 | 워커 → 백엔드 헤더 5종·타임아웃·재전송 → 멱등하게 받기 | §4.7 | 미전달 |
| 9/11 | RETAIN `sentence.finalized` payload `version` = `verdict_version` | §3 | 미전달 |
| 9/11 | `ai.jobs` 권한 → `ai_worker` INSERT 없음, `backend` INSERT·SELECT·UPDATE | §1 | 미전달 |
| 9/11 | job INSERT 규약 → RETAIN payload `{event, verdict_id, comment_id, version}`, TEXT_RETRY `intensities` 선택 | §3 | 미전달 |
| 9/11 | `CaseSnapshot` 은 평면 → `post{…}` 중첩으로 만들지 않는다 | §4.1 | 미전달 |
| 9/11 | `guilty_ratio` 는 0..1 소수 | §4.1 | 미전달 |
| 9/11 | `SentencingDecision.reason_source`·`TextDraft.source` 필수(`AI`/`TEMPLATE`) → finalize 검증 | §5 | 미전달 |
| 9/11 | `IntakeResult.mode` → `FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 내지 않는다 | §4 | 미전달 |
| 9/11 | 길이·배열 상한(라벨 ≤16·`^F\d+$`, `statement` 1~300자 등) → 스키마 기준으로 검증 | §5 | 미전달 |

## 1. 전제 · 아키텍처 · role (proposal2 §1·§19)

```
Frontend ──▶ Main Backend ── 내부 HTTP ──▶ AI API (FastAPI)  : 심문(그래프 A) · 관측(metrics·trace)
                │  게시물/투표/평결/권한 소유 · 결과 확정 API · watchdog · round 예약 · 삭제 무효화
                ▼
            Postgres (공유 인스턴스)
              ├─ app 영역: posts, votes, verdicts, verdict_texts, submissions, meme_images …
              └─ ai  영역: jobs, trial_prep, dossiers, evidence, memory_facts, llm_calls, node_results,
                           privacy_epochs, verdict_commit_records, text_evidence_refs
                     ▲ claim / heartbeat / 중간 결과
              Python Worker ── 그래프 B·C · retain · TEXT_RETRY ── OpenAI / xAI ── Backend 내부 API
```
- **워커는 업무 테이블을 직접 쓰지 않는다.** 최종 저장 권한은 백엔드 내부 API 에만 있다(결정 17)
- role 분리 (9/14 코드 대조: 001~003 파일 끝 `GRANT` 그대로). **파일에 `CREATE ROLE` 은 없다** — 세 role 을 먼저 만들어야 마이그레이션의 `GRANT` 가 통과한다. DELETE 는 어느 role 에도 없다

| role | 스키마 | `ai.jobs`(001) | 002 `trial_prep`·`dossiers`·`evidence`·`evidence_sources`·`banter_examples` | 003 `memory_facts`·`processed_memory_events`·`case_budgets`·`llm_calls`·`node_results` | 004(백엔드 소유) |
|---|---|---|---|---|---|
| `ai_api`(심문·관측) | USAGE | 없음 | 없음 | `case_budgets`·`llm_calls` SELECT·INSERT·UPDATE | 없음 |
| `ai_worker` | USAGE | SELECT·UPDATE(**INSERT 없음**) | 전부 SELECT·INSERT·UPDATE | 전부 SELECT·INSERT·UPDATE | `privacy_epochs` SELECT(가정, §2) |
| `backend` | USAGE | INSERT·SELECT·UPDATE | `evidence`·`dossiers`·`trial_prep` SELECT·UPDATE, `evidence_sources` SELECT(무효화 역조회) | `memory_facts`·`node_results` SELECT·UPDATE | 업무 테이블 전부 + `privacy_epochs`·`verdict_commit_records`·`text_evidence_refs` 쓰기 |

- (원문) `ai_api`(심문용 최소 권한 — DB 는 `ai.case_budgets`·`ai.llm_calls` 쓰기만), `ai_worker`(`ai` 스키마 읽기·쓰기, 단 `jobs` INSERT 불가), `backend`(업무 테이블 전부 + `ai.jobs` INSERT/UPDATE + `ai.privacy_epochs`·`ai.verdict_commit_records`·`ai.text_evidence_refs` 쓰기 + 무효화 SQL 대상 `ai.evidence`·`ai.dossiers`·`ai.trial_prep`·`ai.memory_facts`·`ai.node_results` UPDATE)
- 내부 API 인증: `Authorization: Bearer <SERVICE_AUTH_TOKEN>`, **양방향**(백엔드 → AI API, 워커 → 백엔드). 헤더·타임아웃·거부 본문은 §4.7. 로그에 토큰 금지. **외부 사용자가 임의 `user_id`·`room_id` 로 조회하는 엔드포인트로 노출하지 않는다**
- 공통 규칙(§6.1): ID 는 문자열(신규 AI 테이블 UUID, 기존 업무 ID 는 opaque text). 금액 `amount_krw` 양의 정수. 시각 RFC3339 UTC(`timestamptz`), 화면에서만 KST. 알 수 없는 필드 거부, `schema_version=1`. enum 은 **프론트 값이 표준**(9/8 확정, §15.2 D-21): 강도 `mild|spicy|hell`, 평결 `guilty|notGuilty|agree|disagree|dismissed`, 게시물 `spent|considering`, 형량 `probation|oneDay|life`. 이 문서의 enum 표기도 9/8 CT-07 에서 프론트 값으로 치환 완료(짤 태그 `GUILTY_HEAVY` 등 5종만 기획서 대문자 유지)
- 길이·배열 상한(9/11): 계약에 없던 상한을 채웠다(근거 라벨 ≤16자·`^F\d+$`, `statement` 1~300자 등). 목록은 01 §3.2 공통 규칙, 기계 판독 정본은 `contracts/*-v1.schema.json`

## 2. DB (proposal2 §7.1)

`ai` 스키마·`ai.jobs`(001)·준비·근거·메모리·원장(002·003)은 AI 저장소 마이그레이션 러너가 만든다(적용 절차 §16.3). 아래 **004 는 백엔드 소유**(업무 테이블 변경 + 백엔드가 쓰는 `ai` 테이블 3개). 파일 초안은 AI 저장소 `database/migrations/004_verdict_generation.sql` 에 두고 백엔드 저장소로 옮긴다. (9/14 코드 대조: 004 초안 파일은 아직 AI 저장소에 없다. 아래 표가 현재 정의다)

| 테이블 | 추가 필드·제약 |
|---|---|
| `posts` | `version int`, `audience_version int`, `intake_status`(`PASS`/`UNCLARIFIED`), `intake_source`(`AI`/`FALLBACK`), `submission_id UNIQUE`, `deleted_at`. **`item text NOT NULL`(무엇을, ≤ 30) · `reason` NULL 허용(≤ 200)** |
| `verdicts` | `post_id UNIQUE`, `verdict_version int`, `jury_result`, `policy_snapshot jsonb`(`allowed_sentences[{code,rank}]`, `fallback_sentence`, `reason_required`, `version`), `confirmed_at`, `deadline_at` |
| `verdicts` | `sentence_status`(`PENDING`/`FINAL`), `sentence`, `sentence_source`(`AI`/`RULE`), `sentencing_reason`, `reason_source`(`AI`/`TEMPLATE`) |
| `verdicts` | `text_status`(`PENDING`/`GENERATING`/`TEMPLATE_READY`/`AI_READY`), `text_version bigint DEFAULT 0`, `active_generation_id uuid NULL`, `active_job_id uuid NULL` |
| `verdicts` | `retry_round int DEFAULT 0`, `pending_retry_at`, `applied_intensity`, `meme_image_id` |
| `verdict_texts` | `verdict_id`, `intensity`, `headline`, `statement jsonb`(문장 배열 `{text, kind, evidence_labels}`), `source`(`AI`/`TEMPLATE`), `text_version`, `dossier_id`; **UNIQUE(verdict_id, intensity)** |
| `submissions` | `id`, `actor_id`, `status`(`NEW`/`NEEDS_INPUT`/`COMPLETED`/`BLOCKED`/`EXPIRED`), `payload_hash`, `question_shown bool`, `final_check_count int`, `post_id`, `expires_at`; UNIQUE(actor_id, id) |
| `meme_images` | `tag`(5종) + `strategies text[]`, `emotions text[]`(§11 감정 6종), `keywords text[]`, `is_active` |
| `ai.privacy_epochs` | `scope_key text PRIMARY KEY`(`user:{id}`·`room:{id}`·`post:{id}`), `epoch bigint NOT NULL DEFAULT 0` |
| `ai.verdict_commit_records` | `generation_id uuid PRIMARY KEY`, `request_hash`, `verdict_id`, `text_version`, `committed_at` |
| `ai.text_evidence_refs` | `verdict_id`, `text_version`, `intensity`, `field_path`, `evidence_id uuid REFERENCES ai.evidence(id)` |

- `RETRYING` 상태는 두지 않는다. **재시도 중에도 `text_status=TEMPLATE_READY` 를 유지**해 지금 보여줄 템플릿이 사라지지 않게 한다
- 잠금 순서(전 코드 공유): **privacy scope 행(key 오름차순) → verdict 행 → job 행.** claim/heartbeat 는 job 행만 잠근다. **LLM·내부 HTTP 를 DB 트랜잭션 안에서 기다리지 않는다**
- `ai.privacy_epochs` 권한 가정(9/14 코드 대조): 워커는 `SELECT scope_key, epoch FROM ai.privacy_epochs WHERE scope_key = ANY(:keys)` 로 읽는다. AI 테스트는 `ai_worker` SELECT, `backend` SELECT·INSERT·UPDATE 를 가정한다. **행이 없으면 epoch 0** 으로 본다. 004 초안이 다르면 회신(§14)
- 002 `ai.evidence.fact_type` CHECK 는 `SPEND`·`VERDICT`·`MITIGATION`·`RULE`·`AGGREGATE` 5종이다(9/14 코드 대조). 조서 kind `REASON_ANALYSIS` 는 CHECK 에 없어 저장하지 않는다. DDL 을 넓힐지 05 §3.2 를 줄일지는 AI 파트 결정이고, 넓히면 §16.3 러너 재적용만 필요하다

## 3. 업무 트랜잭션에서 `ai.jobs` INSERT (proposal2 §8.1)

| 업무 트랜잭션 | kind / event_type | dedupe_key | priority | max_attempts | deadline_at | payload | aggregate_id / version |
|---|---|---|---:|---:|---|---|---|
| 게시물 저장(`spent`·`considering` 만, `NO_SPEND` 제외) | `PREPARE` / `post.created` | `prepare:{post_id}:{post_version}:{audience_version}` | 30 | 2 | null | `{post_id, post_version, audience_version}` | `post_id` / `post_version` |
| 배심원 평결 확정(전원 투표 즉시 or 마감 스캔) — **9/14 D-24: PREPARE 종료 뒤 INSERT** | `SENTENCE` / `verdict.confirmed` | `sentence:{verdict_id}:{verdict_version}` | 100 | 2 | **INSERT 시각 + 10s** (원문 `confirmed_at + 10s`) | `{verdict_id, verdict_version, post_id}` | `verdict_id` / `verdict_version` |
| 판결 최초 저장(finalize 또는 watchdog) | `RETAIN` / `sentence.finalized` | `retain:verdict:{verdict_id}:{version}` | 10 | 5 | null | `{event:"sentence.finalized", verdict_id, comment_id:null, version}` — `version` = `verdict_version` (9/14 코드 대조) | `verdict_id` / `version` |
| 템플릿 저장·재시도 필요 | `TEXT_RETRY` / `verdict.text_retry` | `text-retry:{verdict_id}:{verdict_version}:{round}` | 50 | 1 | INSERT 시각 + 20s | `{verdict_id, verdict_version, round, intensities?}` — `intensities` 는 선택 (9/14 코드 대조) | `verdict_id` / `verdict_version` |
| 승인된 댓글(안전 검토 통과) | `RETAIN` / `comment.approved` | `retain:comment:{comment_id}:{version}` | 10 | 5 | null | `{event:"comment.approved", verdict_id:null, comment_id, version}` (9/14 코드 대조) | `comment_id` / `version` |

- (원문) RETAIN payload `{event:"sentence.finalized", verdict_id, verdict_version}` · `{event:"comment.approved", comment_id, comment_version}`, dedupe `retain:verdict:{verdict_id}:{verdict_version}` · `retain:comment:{comment_id}:{comment_version}`, TEXT_RETRY payload `{verdict_id, verdict_version, round, intensities[]}`
- **payload 는 kind 별 모양 그대로, 알 수 없는 필드는 워커가 거부한다**(9/14 코드 대조, AI 저장소 `src/geoji_ai/contracts/jobs.py`). RETAIN 은 `event`·`verdict_id`·`comment_id`·`version` **네 키가 모두 있어야 하고** 쓰지 않는 id 는 `null`. TEXT_RETRY `intensities` 는 빠지면 `target_intensities` 전체를 다시 쓰고, 빈 배열은 거부, `target_intensities` 밖 강도가 있으면 워커가 모델 호출 없이 `generation-failed(SCHEMA_INVALID)`. 일부 강도만 TEMPLATE 이면 그 강도만 넣어 달라(§5 10단계 `(제안)`, §14)
- `schema_version` 컬럼은 기본값 1(001 CHECK `= 1`)

```sql
INSERT INTO ai.jobs (id, event_id, event_type, kind, dedupe_key, aggregate_id, aggregate_version, schema_version, payload, priority, max_attempts, deadline_at, trace_id)
VALUES (gen_random_uuid(), gen_random_uuid(), 'verdict.confirmed', 'SENTENCE', 'sentence:' || :verdict_id || ':' || :verdict_version,
        :verdict_id, :verdict_version, 1, jsonb_build_object('verdict_id', :verdict_id, 'verdict_version', :verdict_version, 'post_id', :post_id),
        100, 2, now() + interval '10 seconds', :trace_id)
ON CONFLICT (dedupe_key) DO NOTHING;   -- 같은 업무 트랜잭션 안. commit 뒤 워커가 250ms 안에 집는다
```
- (9/14 코드 대조) 마감은 `now() + 10s`(D-24, AI 저장소 `scripts/enqueue_job.py` 가 같은 규약으로 넣는다). (원문) `:confirmed_at + interval '10 seconds'`
- **`(제안)` SENTENCE 게이트(9/14 D-24, §15.5):** 평결 확정 시점에 같은 `post_id` 의 `PREPARE` job 이 `QUEUED`·`RUNNING` 이면 SENTENCE 를 바로 넣지 않는다. 백엔드 스케줄러(§6 watchdog 250ms 스캔에 합쳐도 된다)가 **PREPARE 가 종료(`SUCCEEDED`·`FAILED`·`CANCELLED`)되거나 `confirmed_at + 30s` 에 도달하면** 그때 INSERT 하고, `verdicts.deadline_at` 과 job `deadline_at` 을 **INSERT 시각 + 10s** 로 둔다. 기다리는 동안 `sentence_status=PENDING`·`text_status=PENDING`, 공개 API 는 `view=null`(§9 대기 메시지). PREPARE job 이 아예 없으면(재처리 중 삭제 등) 바로 INSERT (9/14 코드 대조) 가짜 백엔드 재현은 PREPARE 를 `payload->>'post_id'` 로 찾고 INSERT 시각은 DB `now()` 다. 로컬 도구 `scripts/enqueue_job.py` 의 SENTENCE 는 기본으로 PREPARE 를 기다린다(`--no-wait-prepare`, `--prepare-wait-seconds` 기본 30) — 운영 INSERT 는 백엔드 몫
- `payload` 에는 **참조(ID·version)만.** 사유·댓글을 작업마다 복제하지 않는다
- `dismissed`(정족수 미달 각하)는 **선고 작업을 만들지 않는다.** `disagree`(살까 말까 부결)는 만든다 — 양형관만 건너뛴다
- 허용 목록이 비었거나 `fallback_sentence` 가 목록에 없으면 **선고 작업을 만들지 않고** 정책 설정 오류를 알린다(§5.3). 생산 환경에 임의 형량의 묵시적 기본값은 없다

## 4. 내부 API (proposal2 §9.2, 서비스 인증 필수)

| API | 소유 | 요청 → 응답 |
|---|---|---|
| `POST /internal/v1/intake` | **AI API** | 백엔드가 호출. `{schema_version, submission_id, payload_hash, mode: INITIAL|FINAL_CHECK, post_type, amount_krw, category, item, reason}` → `IntakeResult{schema_version, mode, status: PASS|NEEDS_CLARIFICATION|BLOCKED, item_review{status, suggested_item}, message, category_review{status, suggested_category, confidence}, injection_detected, intake_source: AI|FALLBACK}`. 동작은 아래 표 (9/14 코드 대조). (원문) 응답 `IntakeResult{status, item_review{status, suggested_item}, message, category_review, injection_detected, intake_source}`. 타임아웃 5초, 실패 = `FALLBACK` 등록 허용 |
| `GET /internal/v1/ai-jobs/{job_id}/snapshot` | 백엔드 | §4.1 |
| `POST /internal/v1/ai-jobs/{job_id}/resolve-evidence` | 백엔드 | §4.2 |
| `POST /internal/v1/verdicts/{id}/begin-generation` | 백엔드 | §4.3 |
| `POST /internal/v1/verdicts/{id}/finalize` | 백엔드 | §5 |
| `POST /internal/v1/verdicts/{id}/generation-failed` | 백엔드 | §4.6 |

intake 동작 (9/14 코드 대조, 07 §3.1·§3.2·§3.4):

| 경우 | 응답 |
|---|---|
| 정상 | OpenAI `MODEL_JUDGMENT` 호출. `intake_source=AI`. 모델 timeout = `INTAKE_TIMEOUT_SECONDS`(기본 4초) − 0.2초 |
| 키 없음 · 벤더 실패 · timeout · 제출 예산 초과 | **200** `status=PASS`·`intake_source=FALLBACK`·`message=""`·`category_review{OK, null, 0.0}` — 등록을 허용한다 |
| 강한 인젝션·무관 텍스트(코드 규칙) | 모델 호출 없이 `status=BLOCKED`·`intake_source=AI`·`message=null`·`injection_detected` 규칙 결과 |
| 필수값 위반(`item` 공백 제거 뒤 1~30자, `reason` ≤ 200, `amount_krw > 0`) | **422** `{"code": "ITEM_LENGTH"|"REASON_LENGTH"|"AMOUNT"}`(9/14 코드 대조, §4.7) |
| 스키마 위반(알 수 없는 필드·enum 밖·길이 상한 초과·잘못된 JSON) | 422 `{"code": "INVALID_REQUEST"}`(9/14 코드 대조). 입력값은 본문에 싣지 않는다 |
| 인증 실패 | 401 `{"code": "UNAUTHORIZED"}`(9/14 코드 대조) |

- `mode=FINAL_CHECK` 는 `NEEDS_CLARIFICATION` 을 내지 않는다(결과에 `mode` 가 있는 이유, 9/11)
- **응답에 `submission_id`·`payload_hash` 에코가 없다.** 요청과 응답을 묶는 것은 호출 쪽에서 한다(01 계약에 필드 없음, 07 §3.1 과 어긋남)
- 제출 임시 예산: AI API 에 `DATABASE_URL` 이 있으면 intake 호출이 `ai.case_budgets`·`ai.llm_calls` 에 `post_id='submission:{submission_id}'` 로 기록, 제출당 cap 1,034 micro-USD(심문 2회분). 초과 시 위 폴백 행. 사건 예산으로 옮기는 것은 §4.4
- (원문 M2 스텁, 9/14 폐기) M2 동안 항상 `{schema_version:1, mode:<요청>, status:PASS, item_review{OK,null}, message:null, category_review{OK,null,1.0}, injection_detected:false, intake_source:FALLBACK}`

### 4.1 snapshot
- 검증: job 존재 ∧ `RUNNING` ∧ 헤더 `X-Generation-Id` 일치 ∧ lease 유효. 아니면 409 `STALE_GENERATION`
- 응답 `CaseSnapshot`(01 §3.2, 정본 `contracts/case-snapshot-v1.schema.json`, **평면** — 9/11 정정. `schema_version, post_id, author_id, post_version, item, reason, amount_krw, category, post_type, created_at` 이 최상위. (원문) `post{id, author_id, post_version, item, reason, amount_krw, category, post_type, created_at}`), `audience{room_ids, audience_version, public_share_enabled}`, `privacy_versions[{scope_key, epoch}]`(관련 scope 전부: post·author·각 room), `room_snapshots[{room_id, intensity, rule_version}]`, `intake_result`(없으면 null), `jury`(SENTENCE·TEXT_RETRY 일 때, 아니면 null: `verdict_id, verdict_version, result, vote_counts, guilty_ratio(0..1 소수, 백분율 아님), confirmed_at, deadline_at, policy{version, allowed_sentences[{code, rank}], fallback_sentence, reason_required}, target_intensities, default_intensity`)
- **`(제안)` RETAIN job 확장**(9/14 코드 대조: 워커 구현됨, 스키마에 선택 필드로 들어감 — 기존 스냅샷은 그대로 유효, `schema_version` 1): `sentence.finalized` 면 `verdict_final{sentence, sentence_source, sentencing_reason, reason_source, applied_intensity, banter_strategy}` + `jury`, `comment.approved` 면 `comment{comment_id, version, room_id, post_id, post_status, author_id, content(≤ 1000자), created_at}`
  - 확장 필드가 **없으면 워커는 행 0 으로 complete — 기억이 쌓이지 않는다.** 댓글 기억은 댓글 방이 `room_snapshots` 에 있어야 저장된다
  - `comment.content` 가 1000자를 넘으면 스냅샷 전체가 거부된다
  - 삭제된 원본이면 **404** — 워커는 skip(complete). 409 등 다른 거부는 skip 이 아니라 오류로 재시도한다

### 4.2 resolve-evidence
- 요청 `{candidates[{source_type, source_id, source_version, score}], include: ["rules","aggregates","recent_verdicts","style_comments"]}` (후보 ≤ 20)
- **자유 조회 API 가 아니다.** job 이 가리키는 사건의 작성자·대상 방·공개 정책에 맞는 것만 반환. 현재 권한·원본 버전으로 걸러 stale 후보는 제외
- 응답 `{sources[{source_type, source_id, source_version, payload, scope{visibility: PUBLIC|ROOMS|PRIVATE, room_ids[]}}], aggregates{burn_rate, tier, no_spend_days, repeat_same_category_30d, excludes_post_id, window{start_at, end_at}, rule_version}, room_rules[{room_id, rule_id, version, text}], recent_verdicts[{post_id, post_version, category, amount_krw, reason, result, sentence, judged_at, scope}], style_comments[{comment_id, room_id, content, created_at}]}` — `style_comments` 는 `ROOM_COMMENT_STYLE_ENABLED` 일 때만 채운다(P0 빈 배열, §15.3 D-04). 다섯 키 모두 필수, 알 수 없는 필드 거부
- 반복 집계 규칙: 현재 사건 제외, 사건 생성 시각 이전 30일, 같은 카테고리 확정 소비 건수. 항목 단위(택시 횟수) 숫자는 만들지 않는다
- (9/14 코드 대조) AI 파트 가정 두 가지, 회신 대기(§14): `aggregates.burn_rate` 는 0~1 비율로 읽는다. `recent_verdicts[]` 에 `verdict_id` 가 없어 PRIOR 근거 출처를 `POST/{post_id}/{post_version}` 으로 둔다

### 4.3 begin-generation
- 요청 `{job_id, generation_id, verdict_version}`. 잠금 순서(§2)로 verdict·job 을 잠그고 lease·generation·평결 버전 확인 → `active_job_id`·`active_generation_id` 설정. 같은 현재 generation 재호출 허용. 다른 활성 작업이 유효하면 409
- SENTENCE 는 **마감 전 `PENDING`** 에서만, TEXT_RETRY 는 **템플릿이 이미 노출된 `FINAL`** 에서만 시작
- 응답 `{fixed_sentencing: {sentence, sentencing_reason, reason_source} | null, text_version, deadline_at}` — FINAL 이면 고정값을 준다(워커는 양형관을 부르지 않는다). `deadline_at` 은 늘 채운다
- 거부(9/14 코드 대조, AI 저장소 가짜 백엔드 기준 — 실제 구현이 다르면 회신 §14): `verdict_version` 불일치·이미 실패 보고한 generation·다른 활성 generation 유효·요청 job 이 `RUNNING ∧ generation 일치 ∧ lease 유효` 가 아님·`AI_READY` 인데 TEXT_RETRY → 409 `STALE_GENERATION`. PENDING 인데 마감 지남 → 409 `DEADLINE_EXCEEDED`. verdict 없음 → 404

### 4.4 `(제안)` 제출 → 게시물 매핑 통지
- `POST /post-submissions/{id}/complete` 가 post 를 만들 때 AI API `POST /internal/v1/submissions/{id}/linked {post_id}` 를 호출하거나, PREPARE job payload 에 `submission_id` 를 넣는다. 심문 임시 예산(`submission:{id}` 키, §4)을 사건 예산으로 이전하는 데 필요(06 §3.2). 없으면 제출 예산은 별도로 남는다
- (9/14 코드 대조) 두 방식 모두 AI 쪽에 아직 없다. `/linked` 엔드포인트 없음, `PreparePayload` 는 `submission_id` 를 받지 않는다(알 수 없는 필드 거부). 채택 회신 뒤 AI 파트가 붙인다

### 4.5 `(제안)` trace 조회
- 데모 C 관측 화면용. AI API `GET /internal/v1/trials/{post_id}/trace`(서비스 인증) 를 백엔드가 프록시하거나 내부망에서 프론트가 직접. 응답: dossier 라벨·recall 출처·노드 타임라인·비용(원문 없음)
- (9/14 코드 대조) **AI API 쪽은 구현됐다**(§16.2). 응답은 최신 dossier 라벨·fact_type·scope, 출처 개수, 노드 타임라인, 비용 — 원문·개별 id 없음. 기록이 없으면 404 `{"code": "TRACE_NOT_FOUND"}`. 남은 미결은 **프록시 주체**(§14)

### 4.6 generation-failed · 오류 코드 표 `(제안 — proposal2 에 코드 표 없음)`
- 요청 `{job_id, generation_id, error_code}`. 현재 세대만 처리, 다른 세대는 무시(409 `STALE_GENERATION`). **같은 세대·같은 코드 재전송은 200**(9/14 코드 대조, 가짜 백엔드). 워커가 보내는 코드는 아래 8종뿐이다(AI 저장소 `domain/retries.py`)

| error_code | 백엔드 처리 |
|---|---|
| `AI_NOT_READY`(M2 스텁) · `POLICY_ERROR` · `EVIDENCE_INVALIDATED` | 즉시 폴백(형량 `fallback_sentence` FINAL/RULE, 문구 TEMPLATE, RETAIN job). **TEXT_RETRY 예약 안 함** |
| `VENDOR_UNAVAILABLE` · `BUDGET_EXCEEDED` · `EVAL_FAILED` · `SCHEMA_INVALID` · `DEADLINE_EXCEEDED` | 같은 폴백 + `TEXT_RETRY` round 1 예약(§7) |
| TEXT_RETRY 중 실패 | 템플릿 유지, 다음 round 예약(남았으면), 마지막이면 운영 알림 |

- (9/14 코드 대조) 가짜 백엔드가 이 표 그대로 구현했다(최초 확정이면 FINAL/RULE·`TEMPLATE_READY`·RETAIN INSERT, 2행 코드면 round 예약). 채택 회신 대기(§14)
- (9/14 코드 대조) 서기가 AI 문구를 하나도 못 냈을 때 코드 우선순위: 예산 초과 → `BUDGET_EXCEEDED`, 시간 초과(또는 시간 예산으로 시작 못 한 강도) → `DEADLINE_EXCEEDED`, 그 밖 → `VENDOR_UNAVAILABLE`. 2행끼리라 분기는 같고 집계 분포만 달라진다
- (9/14 코드 대조) **TEXT_RETRY round 안의 실패는 저장하지 않는다.** 서기·검증·검수가 실패하면 TEMPLATE finalize 없이 `generation-failed`(`EVAL_FAILED`·`DEADLINE_EXCEEDED` 등)만 보낸다. 다음 round 예약은 백엔드(§7)

### 4.7 공통 규약 — 인증 · 헤더 · 타임아웃 · 거부 본문 (9/14 코드 대조)

- **인증은 양방향이다.** 백엔드 → AI API, 워커 → 백엔드 모두 `Authorization: Bearer <SERVICE_AUTH_TOKEN>`(같은 값). AI API 는 `/health/*` 만 무인증이고, 토큰 설정값이 비어 있으면 전부 401 이다(열어 두지 않는다). 비교는 timing-safe
- 워커 → 백엔드 헤더 5종: `Authorization`, `X-Trace-Id`(job 의 `trace_id`), `X-Request-Id`(**시도마다** 새 uuid4), `X-Job-Id`, `X-Generation-Id`. 본문이 있으면 `Content-Type: application/json`
- 워커 타임아웃: connect 0.5초 공통, read 는 snapshot 2 · resolve-evidence 2 · begin-generation 1 · finalize 3 · generation-failed 1초
- 워커 재전송: transport 오류·timeout·5xx 에 **같은 본문 바이트**를 최대 2회(200ms·600ms 뒤). 그래도 실패면 job 에 `BACKEND_UNAVAILABLE`, 5초 뒤 재시도. **4xx 는 재전송하지 않는다** — 백엔드는 같은 요청 재도착을 멱등하게 받는다(finalize 는 commit record, §5)
- **거부 응답 본문은 양쪽 모두 `{"code": "<코드>"}` 다**(9/14 결정, §15.5). 백엔드 → 워커 거부와 AI API → 백엔드 거부가 같은 모양이다
- 워커는 백엔드 4xx 본문의 `code` 를 읽고, 없으면 `HTTP_<status>` 로 본다. 파서는 옛 모양 호환으로 `error_code`·`detail.code` 도 여전히 읽지만 계약 모양은 `{"code"}` 하나다. AI 저장소 가짜 백엔드도 `{"code"}` 를 쓴다. 이 문서에 이름이 없던 코드 — 401 `UNAUTHORIZED` · 404 `NOT_FOUND` · 422 `INVALID_REQUEST`(begin·failed·resolve 본문 검증) — 는 가짜 백엔드가 정한 것이라 확정 회신 대기(§14)
- AI API 가 내는 거부(9/14 코드 대조): 401 `UNAUTHORIZED`(`WWW-Authenticate: Bearer` 헤더 유지), intake 422 `ITEM_LENGTH`·`REASON_LENGTH`·`AMOUNT`, 본문 스키마 위반 422 `INVALID_REQUEST`(검증 오류 원문·입력값 없음), trace 404 `TRACE_NOT_FOUND`, trace 인데 `DATABASE_URL` 없음 503 `DB_UNAVAILABLE`. `/health/ready` 503 은 거부가 아니라 상태 보고라 본문이 다르다(§16.2). 없는 경로 404 는 FastAPI 기본 `{"detail": "Not Found"}`
- (원문, 9/14 폐기) AI API 가 내는 거부는 FastAPI 모양 `{"detail": {"code": "…"}}` 이다

## 5. finalize (proposal2 §10)

요청 `FinalizeRequest`(01 §3.2, 정본 `contracts/finalize-v1.schema.json`): `schema_version`·`job_id`·`generation_id`·`verdict_version`·`expected_text_version`·`dossier_id`·`privacy_versions`·`draft_hash`·`sentencing{sentence, sentencing_reason, reason_source, evidence_labels, aggravating, mitigating} | null`·`draft{texts[{intensity, headline, statement, banter_strategy, selected_candidate_id, attack_angle, source}], meme_tag, meme_hints{emotion, keywords}}`·`evaluation`·`evaluation_draft_hash`·`prompt_bundle_version`·`guardrail_policy_version`·`model_ids{sentencing, writer, evaluator}`. 전부 필수(`sentencing` 은 null 가능), 알 수 없는 필드 거부 (9/14 코드 대조: `schema_version`·`model_ids` 모양 추가)

- (9/11) `sentencing.reason_source` 와 `texts[].source` 는 둘 다 `AI | TEMPLATE` 필수
- (9/14 코드 대조) `meme_hints.emotion` 은 문자열(≤ 30)이 아니라 **6종 enum** `DISAPPROVAL`·`ABSURD_SERIOUSNESS`·`SMUG`·`PITY`·`CELEBRATION`·`RESIGNATION`(writer-draft-v1·finalize-v1, `schema_version` 1). `meme_hints.keywords` 는 ≤ 10개·각 ≤ 30자. §11 짤 점수 `+2` 대조값
- `guardrail_policy_version` 은 `guardrail-v1 | guardrail-v2`, 설정값(§15.3 D-07)과 달라야 거부

**`draft_hash` 규칙 (9/14 코드 대조, AI 저장소 `src/geoji_ai/domain/draft_hash.py`).** `draft_hash` 와 `evaluation_draft_hash` 는 같은 규칙이고 백엔드가 다시 계산해 요청 값과 비교한다.
1. `{"draft": <WriterDraft>, "sentencing": <SentencingDecision 또는 null>}` 객체
2. 모든 문자열(키와 값)을 유니코드 NFC 로 정규화. 배열 순서는 유지
3. 키 코드포인트 순 정렬, 구분자 `,` `:`(공백 없음), 비 ASCII 이스케이프 없음 JSON
4. UTF-8 바이트의 sha256 **소문자 hex**(`^[0-9a-f]{64}$`)

검수 뒤 문구를 한 글자라도 고치면 hash 가 바뀌어 finalize 가 거부한다.

**백엔드는 호출자가 보낸 평결·default intensity·허용 목록·`sentence_source` 를 신뢰하지 않는다. DB 스냅샷에서 읽는다.** 문구·검수 hash 일치, 근거 라벨→UUID(`ai.dossiers.label_map`), 길이(30/200/100), 강도 집합 = `target_intensities`(TEXT_RETRY 는 ⊆, 아래 10단계), `guardrail_policy_version` = 설정값 같은 결정적 검증을 서버에서 다시 실행한다. 의미 검수는 AI 책임이지만 **보고서 누락·`false` 는 백엔드가 거부한다.**

```text
BEGIN
  1. 같은 generation 의 commit record 가 있으면: request_hash 일치 → 이전 성공 응답 / 불일치 → 409 IDEMPOTENCY_CONFLICT
  2. privacy scope(key 오름차순) → verdict → job 순으로 잠금
  3. commit record 재확인(동시 동일 요청 흡수). 원본 삭제 여부·현재 privacy epoch·audience version 재확인
  4. job RUNNING ∧ lease 유효 ∧ generation 일치
  5. verdict_version · active_job_id · active_generation_id · expected_text_version 일치
  6. 최초 SENTENCE 는 DB now() < deadline_at. TEXT_RETRY 는 job 자체 제한 시각
  7. 허용 목록·결과·출력 구조·근거 라벨→UUID 매핑·검수 대상 hash 검사
  8. sentence PENDING 이면 검증된 후보로 FINAL 한 번만 갱신 (sentence_source=AI). FINAL 이면 입력 후보가 기존 형량과 같아야 함
     sentencing_reason 은 FINAL 이후 변경 불가 — reason_source=TEMPLATE 치환만 허용 (D-19)
  9. texts 를 새 text_version 으로 원자적 저장 (verdict_texts, source 포함). text_evidence_refs 도 같은 version
 10. text_status = AI_READY (모든 강도 AI) / TEMPLATE_READY 유지 + 재시도 대상 (일부 강도 TEMPLATE, (제안) TEXT_RETRY payload intensities[] = TEMPLATE 강도)
     짤 선택 (§11) → meme_image_id 고정. active generation 해제
 11. 최초 형량 확정 때만 RETAIN job INSERT ... ON CONFLICT DO NOTHING
 12. job SUCCEEDED, commit record INSERT
COMMIT
```
- **`(제안)` TEXT_RETRY 강도 집합(9/14 코드 대조):** TEXT_RETRY finalize 의 `texts` 는 payload `intensities` 에 든 강도만 온다. 검증을 "강도 집합 = `target_intensities`" 가 아니라 **중복 없음 ∧ ⊆ `target_intensities`** 로 완화하고, 받은 강도 행만 갱신·나머지 강도 행은 유지해 달라. `text_status` 는 갱신 뒤 전체 강도가 AI 면 `AI_READY`. 가짜 백엔드는 이렇게 구현했고 FINAL 재생성에서 형량·양형 이유가 기존과 다르면 422 `INVALID_DRAFT`. 채택 회신 대기(§14)
- 가짜 백엔드 검사 순서(9/14 코드 대조, 실제 구현 비교용): commit record(`request_hash` = 요청 본문 바이트 sha256) → 스키마 → verdict 존재(404) → generation·job·`verdict_version`·`expected_text_version`(409 `STALE_GENERATION`) → 최초 마감(409 `DEADLINE_EXCEEDED`) → hash 형식·`guardrail_policy_version`(422) → 형량 → 강도 집합 → 저장·RETAIN·commit record

| 결과 | HTTP |
|---|---|
| 저장 성공 · 동일 성공 재전송 | 200 `{verdict_id, text_version, committed_at}` |
| 다른 generation 활성 | 409 `STALE_GENERATION` — 워커는 결과 폐기 |
| 같은 generation 다른 본문 | 409 `IDEMPOTENCY_CONFLICT` |
| Evidence 삭제·공개 범위 변경 | 409 `EVIDENCE_INVALIDATED` — 초안 폐기, 새 근거로 별도 작업 |
| 최초 노출 마감 초과 | 409 `DEADLINE_EXCEEDED` — watchdog 폴백 |
| 미확정 형량·schema·검수 오류 | 422 `INVALID_DRAFT` — 워커 보정 횟수 남으면 보정. 형량 규칙 위반 전용 코드를 따로 둘지는 회신 대기(§14, 지금은 알림을 가를 수 없다) |
| DB·네트워크 장애 | 워커가 같은 요청 재전송(§4.7) → commit record. **모델 재호출 없음** |
- 첫 성공 후 삭제가 일어났어도 commit record 는 **commit 사실만** 돌려준다. 삭제 전 민감 문구를 응답 캐시로 다시 주지 않는다. 최종 문구는 권한 검증된 GET 으로
- 첨부 사진은 일반 게시물 이미지. 구매 증빙·금액 근거로 자동 가정하지 않는다

## 6. deadline watchdog (proposal2 §11.1)

백엔드 독립 스케줄러, **250ms 주기**로 마감 초과 `PENDING` 판결을 찾는다. 여러 인스턴스여도 §2 잠금 + 조건부 갱신으로 한 번만 확정.
1. privacy scope · verdict · active job 을 같은 순서로 잠근다
2. 이미 `AI_READY` 또는 `FINAL + TEMPLATE_READY` 면 반복하지 않는다
3. 미확정 형량을 `policy_snapshot.fallback_sentence` 로 `FINAL` 확정, `sentence_source=RULE`, 이유·문구는 `TEMPLATE`(§10 파일, 결과별)
4. `active_generation_id` 비우고 이전 job `CANCELLED`
5. `RETAIN` 과 `TEXT_RETRY` round 1 을 같은 트랜잭션에 기록
6. 이전 워커 응답은 generation/상태 불일치로 거부
**엄밀한 10,000ms 보장은 아니다.** `confirmed_at → 첫 노출 저장` p95 를 측정하고 프론트 표시 지연을 별도로 합산한다.
- 9/14 D-24: 마감 기준은 SENTENCE INSERT 시각이다(§3 게이트). PREPARE 대기(최대 30초) 동안은 watchdog 대상이 아니다(`deadline_at` 이 아직 없다). 체감 지연 = PREPARE 대기 + 10초 상한
- (9/14 코드 대조) 4단계 이전 job 상태가 08 §3.1(늦게 끝난 워커의 `complete`)과 어긋난다. AI 테스트는 **`CANCELLED`**(워커 complete 는 0행)로 고정했다. 실제 구현 확인 회신 대기(§14)

## 7. 재시도 round · reaper (proposal2 §8.3·§11.2)

- round 1 은 템플릿 후 5분, round 2 는 10분, round 3 은 20분 뒤 `TEXT_RETRY` INSERT(§3 규약, `retry_round <= 3`, `(verdict_id, verdict_version, round)` 중복 방지). 스캔 주기 5분이라 최대 한 주기 추가 지연
- 최초 저장 형량·양형 이유 고정. 문구만 갱신. 성공 시 이미지·형량·이유 유지, `texts`·`source`·`text_version` 만 변경. 마지막 실패·비용 한도 초과는 템플릿 유지 + 운영 알림
- round 안 실패는 워커가 저장 없이 `generation-failed` 만 보낸다(§4.6). reaper 가 `FAILED` 로 바꾼 TEXT_RETRY 도 다음 round 예약은 백엔드 몫
- reaper(5초): `02-jobs-queue-lease.md` §3.5 SQL — lease 만료 회수. PREPARE/RETAIN 은 attempts 안에서 `QUEUED`, SENTENCE 는 마감 전만, TEXT_RETRY 는 `FAILED`(다음 round 예약). 원문 SQL(9/14 코드 대조, AI 저장소 `src/geoji_ai/adapters/postgres_jobs.py` `REAPER_SQL`, 백엔드 스케줄러로 복사):

```sql
-- 5초 주기. lease 만료 회수. SENTENCE 는 마감 전만 되살린다
UPDATE ai.jobs SET status = CASE
    WHEN kind = 'TEXT_RETRY' THEN 'FAILED'
    WHEN kind = 'SENTENCE' AND (deadline_at IS NULL OR deadline_at <= now()) THEN 'CANCELLED'
    WHEN attempts >= max_attempts THEN 'FAILED'
    ELSE 'QUEUED' END,
  owner_id = NULL, generation_id = NULL, lease_until = NULL,
  last_error_code = COALESCE(last_error_code, 'LEASE_EXPIRED'), updated_at = now()
WHERE status = 'RUNNING' AND lease_until < now();
```
- 운영에서 워커의 `--reaper`(같은 프로세스 회수)는 개발·로컬 전용이라 끈다. SENTENCE `CANCELLED` 뒤 폴백은 §6 watchdog
- `lease_expired_total` 지표는 워커가 관측하지 못한다. 운영 reaper 가 회수 건수를 kind 별로 로그·지표에 남겨야 생긴다(08 §3.3, 회신 §14)

## 8. 삭제 · 권한 변경 (proposal2 §11.3)

- 원본 삭제·댓글 삭제·작성자 탈퇴·방 공유 철회: **해당 scope 의 `ai.privacy_epochs` 를 먼저 잠그고 증가**, 같은 트랜잭션에서 원본 비활성화, 무효화 작업 기록. 판결 생성은 이전 epoch 로 저장할 수 없다(finalize 3 단계)
- **`(제안)` 진행 중 작업 끄기(9/14 D-26, §15.5):** 같은 무효화 트랜잭션에서 영향받는 게시물의 `ai.jobs` 중 `QUEUED`·`RUNNING` 인 `PREPARE`·`SENTENCE`·`TEXT_RETRY` 를 `CANCELLED` 로 바꾼다(게시물 삭제·공유 철회는 그 `post_id`, 작성자 탈퇴는 그 사용자의 게시물 전부). 워커는 heartbeat 소유권 조건(`status='RUNNING'`, 02 §3.2)이 깨지는 즉시 핸들러를 취소하고 원장 예약을 닫는다. 추가 방어로 워커는 모델 호출 직전마다 epoch 를 확인해 달라졌으면 호출하지 않는다 (9/14 코드 대조) TEXT_RETRY payload 에는 `post_id` 가 없어 `verdict_id` → post 로 찾는다. 워커는 다음 heartbeat(기본 5초) 안에 멈추고, 그 사이 나간 모델 호출은 원장 `UNKNOWN`(예약액은 `ledger-sweep` 까지 유지, 06 §3.2)
- **파생 정리는 비동기여도 읽기 차단은 즉시.** 판결 조회는 현재 epoch 와 저장 당시 epoch 를 비교, 불일치면 과거 문구 대신 공개 가능한 템플릿. 캐시·공유 카드 캐시도 버전 키가 달라지게
- 무효화 스케줄러: `04-memory-evidence-deletion.md` §3.5 SQL(evidence_sources → evidence/dossiers/trial_prep/memory_facts/node_results) + `text_evidence_refs` 로 영향받는 `verdict_texts` 를 템플릿으로 전환. 재시도는 삭제된 prep 를 읽지 않는다. **이미 `FINAL` 인 형량은 설명 삭제와 별개로 유지.** 사건 삭제 시 판결 조회도 차단
  - (9/14 코드 대조) SQL 파일은 AI 저장소 `database/sql/invalidate_scope.sql`(백엔드로 복사, §16.3). **한 트랜잭션**으로 실행, 바인드 `:t`(source_type)·`:id`(source_id)·`:scope_key`. `node_results` 조건은 04 원문 `privacy_versions ? :scope_key` 가 아니라 `privacy_versions @> jsonb_build_array(jsonb_build_object('scope_key', CAST(:scope_key AS text)))` 다(객체 배열이라 `?` 는 매치되지 않는다, 9/14 사용자 승인). `text_evidence_refs` → 템플릿 전환은 이 파일에 없고 백엔드가 쓴다
- 통합 테스트 대상: 다른 방 기록·댓글 삭제까지(scope 누락 시 epoch 검사가 무의미)

## 9. 공개 API (proposal2 §9.1·§9.3)

| API | 요청·응답 | 오류 |
|---|---|---|
| `POST /post-submissions` | 무엇을(필수 ≤ 30)·사유(선택 ≤ 200)·금액·종류·카테고리·공유 방 → `{submission_id, status, intake_result}` 또는 `post_id`. 내부에서 `/internal/v1/intake(mode=INITIAL)`(동작 §4) | 400 입력, 401, 403 공유 권한, 429 |
| `POST /post-submissions/{id}/complete` | `{action: REVISE|PROCEED, 최종 값, revision}`. `REVISE` 는 `/internal/v1/intake(mode=FINAL_CHECK)` 1회. **`BLOCKED` 를 `PROCEED` 로 우회 불가(409).** 중복 완료는 기존 post 반환 | 409 만료·버전 충돌·차단 |
| `GET /posts/{id}/verdict?room_id=` | `verdict-view-v1`: `schema_version, post_id, jury_status, sentence_status, text_status, text_version, view|null, poll_after_ms` (9/14 코드 대조: `schema_version`·`post_id` 추가, 정본 `contracts/verdict-view-v1.schema.json`). 방 강도 행, 없으면 `applied_intensity`(최다 투표 방, 동률 방 생성일). 생성 중 200 + `view=null` | 404 없음/권한 없음 |
| `GET /posts/{id}/share-card` | 공개 허용 문구·이미지 metadata 만. **Evidence 원문·개인 이력 반환 금지.** `PUBLIC` 근거 문구만 | |

- 제출 상태 `NEW → NEEDS_INPUT → COMPLETED`, `BLOCKED`, `EXPIRED`. 최초 `PASS` 는 `NEW → COMPLETED`. `payload_hash` = 정규화된 타입·금액·무엇을·사유·카테고리·공유 방 목록. 질문은 제출당 1회(`question_shown`)
- **전달 방식은 폴링으로 확정(9/8, §15.2). Realtime · SSE 없음.** 프론트 폴링: 1초 → 15초 뒤 5초, 화면 이탈 시 취소, `AI_READY` 면 즉시 중단, 템플릿 후 30초 또는 재진입. **`text_version` 이 작은 응답으로 UI 를 덮지 않는다.** 빈 화면 대신 대기 메시지
- `source=TEMPLATE` 이면 AI 판사 라벨·양형 이유 블록 숨김. 지옥맛 방장 확인 문구: "지옥맛은 반말과 욕설, 인격 조롱이 나옵니다. 멤버 전원이 동의했는지 확인해주세요."

## 10. 템플릿 공유 파일

`contracts/fixtures/templates-v1.json`(AI 저장소, 백엔드에 복사·버전 고정): 결과별 `{headline, statement[], sentencing_reason_template}`. 유죄 "배심원 {n}인 중 {m}인이 유죄로 판단했습니다. 형량: {sentence_label}" / 무죄 "배심원단은 이 지출에 정상 참작의 여지가 있다고 판단했습니다." / 동의 "배심원단이 구매를 승인했습니다. 후회는 본인 몫입니다." / 기각 "배심원단이 구매를 기각했습니다. 지갑을 닫으십시오." 형량 라벨: `집행유예` / `징역 1일 (내일 하루 무지출)` / `무기징역 (3일 무지출)`. watchdog·generation-failed·부분 강도 템플릿 모두 이 파일. **치환 토큰(9/11 확정)**: `{n}` 배심원 수 · `{m}` 유죄 표 수 · `{sentence_label}` 형량 라벨(같은 파일 `sentence_labels` 표). 파일 형태는 `{version, sentence_labels{probation, oneDay, life}, results{guilty|notGuilty|agree|disagree: {headline, statement[], sentencing_reason_template}}}`. `headline` 은 프론트 `VERDICT_LABELS`(유죄·무죄·동의·기각), `sentencing_reason_template` 은 유죄만 `"형량: {sentence_label}"` 나머지 null. (원문) 토큰은 `{형량 라벨}` 이었으나 공백·한글이 든 이름은 `str.format` 으로 채울 수 없어 바꿨다.
- (9/14 코드 대조) 파일과 일치. `version` 값은 `"templates-v1"`

## 11. 짤 점수 선택 (finalize 10 단계, proposal2 §17)

`meme_images` 중 `tag == draft.meme_tag ∧ is_active` → `+3` `banter_strategy ∈ strategies` · `+2` `meme_hints.emotion ∈ emotions` · `+1` `|keywords ∩ meme_hints.keywords|` · `−5` 같은 사용자 최근 노출 5장 → `crc32(post_id + image_id)` tie-break → `meme_image_id` 고정(새로고침 불변). 후보 0 → 결과별 기본 이미지. 감정 어휘 6종: `DISAPPROVAL` `ABSURD_SERIOUSNESS` `SMUG` `PITY` `CELEBRATION` `RESIGNATION`. 문구 합성은 클라이언트 캔버스, 이미지는 CORS 허용 CDN.
- (9/14 코드 대조) 6종은 이제 계약 enum 이다(§5). 이 밖의 값은 finalize 요청에 오지 않는다. `meme_images.emotions` 도 이 6종으로 채운다

## 12. 시드 (9/17, proposal2 §18 데모 시드 생성기)

| 항목 | 수량 | 조건 |
|---|---:|---|
| 방 | 3 | 순한맛·매운맛·지옥맛 각 1, 마감 30분, 규칙 3~5개(택시·배달·커피 프리셋) |
| 사용자 | 4 | 데모 C 사용자 1명은 세 방 모두 |
| 게시물 | 12 | 카테고리 6 × 2. 판결 확정 10(유죄 6·무죄 2·동의 1·기각 1) + 투표 중 2. 데모 C 사용자 스타벅스 2건 → `post_id` 를 AI 파트에 전달(04 시드) |
| 댓글 | 20 | 판결 확정 글, 21자 이상, 방 강도 말투 |
| 판결 | 10 | **실제 파이프라인 통과**(PREPARE → 투표 → SENTENCE → finalize). ≈ 250원 |

- AI 파트 시드는 백엔드 시드 뒤에 AI 저장소에서 돈다(9/14 코드 대조, 명령은 §16.3). 백엔드가 넘길 것: 데모 C 사용자 id·방 id, 스타벅스 post 2개 `post_id`·`verdict_id`, 시드 기준 시각(§14). `meme_catalog` 은 만들지 않는다 — 짤 메타는 백엔드 `meme_images` 소유

## 13. 백엔드 수용 검사 (proposal2 §20 먼저 실패시킬 케이스 중 백엔드 몫)

| 작업 | 케이스 | 기대 |
|---|---|---|
| 3 | 마지막 표 동시 도착 | 평결 커밋 1회, SENTENCE job 1개(dedupe), 형량 FINAL 1회 |
| 3 | 평결 commit 직후 백엔드 프로세스 중단 | job 은 커밋돼 있고 워커가 처리. watchdog 이 중복 확정하지 않음 |
| 3 | finalize 응답 유실 후 재전송 | commit record 로 같은 200, `text_version` 동일 |
| 7 | 중복 완료 요청 | 기존 post 반환, PREPARE job 1개 |
| 7 | `PROCEED` 로 `BLOCKED` 우회 | 409 |
| 7 | 검토 후 사유 교체 | `REVISE` 가 `FINAL_CHECK` 를 거쳐 `BLOCKED` 가능 |
| 7 | 질문 두 번 노출 | `question_shown` 로 차단 |
| 8 | 템플릿 이후 늦은 성공 | 409 `STALE_GENERATION` |
| 8 | retry 중 삭제 | epoch 불일치 → 409 `EVIDENCE_INVALIDATED`, 조회 즉시 템플릿 |
| 8 | 9초 시점 워커 종료 | watchdog 10초 폴백 1회, job `CANCELLED`, RETAIN·round1 기록 |

## 14. 미결 (백엔드 회신 필요)

9/14 옛 백엔드 전달 파일 §5 의 회신 대기 표를 합쳤다(중복 행은 하나로).

| ID | 항목 | 기한 |
|---|---|---|
| ~~D-20~~ | **확정(§15.2)** — Supabase 인스턴스 공유. 남은 것: `ai_api`·`ai_worker`·`backend` role 생성·grants(§1 표), Session Pooler 접속 정보 | 9/9(지남) |
| ~~D-21~~ | **확정(§15.2)** — 프론트 값 표준. 남은 것: `rooms.spice_level` 값 변경 | 9/9 |
| ~~enum 매핑~~ | **확정(§15.2)** — 카테고리 11종 고정 | 9/9 |
| CaseSnapshot | `post_version`·`audience_version`·`privacy_versions`·`rule_version`·`policy{…}`, RETAIN 확장 `verdict_final`·`comment` 를 채울 수 있는가. **9/11 서버 `d0f9fd5` 확인: 재판 API 는 생겼으나 위 필드·`/internal/v1/*` 전부 없음.** 계약은 01 §3.2 대로 확정, 검증은 03 가짜 백엔드 | 9/9(지남) |
| 004 `privacy_epochs` | §2 의 테이블 정의·권한 가정(`ai_worker` SELECT, `backend` SELECT·INSERT·UPDATE, 행 없으면 epoch 0)이 004 초안과 같은가 | 미정 |
| 내부 API 상태 기계 | §4.3·§4.6·§5 의 가짜 백엔드 기준 동작과 실제 구현이 같은가 | 미정 |
| 거부 응답 본문 | 401·404·409·422 본문이 `{"code": …}` 인가, 401·404·422 코드 이름(§4.7) | 미정 |
| §4.2 `burn_rate` 단위 | 0~1 비율인가(AI 는 0~1 로 가정해 문장을 만든다) | 미정 |
| §4.2 `recent_verdicts[].verdict_id` | `verdict_id`·`verdict_version` 을 넣어 줄 수 있는가(없어서 PRIOR 출처를 `POST/{post_id}/{post_version}` 로 둔다) | 미정 |
| §4.4 | 제출 → 게시물 매핑 통지 방식 | 9/14 |
| §4.5 | trace 조회 프록시 주체 — AI API 엔드포인트는 구현됨 | 9/16 |
| §4.6 | `generation-failed` 오류 코드 표 채택(가짜 백엔드가 표대로 구현) | 9/10(지남) |
| §5 10단계 | TEXT_RETRY 에서 강도 집합 검증을 `⊆ target_intensities` 로 완화하고 `payload.intensities[]` 에 TEMPLATE 강도를 넣어 줄 수 있는가 | 9/13(지남) |
| §5 422 코드 | `INVALID_DRAFT` 외에 형량 규칙 위반 전용 코드를 둘 수 있는가 | 미정 |
| §6 4단계 | watchdog 뒤 늦게 끝난 이전 job 을 `CANCELLED`(§6)로 두는가, 워커 `complete`(08 §3.1)인가 | 미정 |
| §7 `lease_expired_total` | 운영 reaper 회수 건수를 kind 별 로그·지표로 남길 수 있는가 | 미정 |
| §12 데모 C 시드 식별자 | 스타벅스 post 2개 `post_id`·`verdict_id`, 사용자·방 id, 시드 기준 시각(§16.3 시드 입력) | 9/17 |
| D-24 `(제안)` | SENTENCE 게이트 — PREPARE 종료 또는 확정 + 30초까지 INSERT 대기, 마감은 INSERT + 10초(§3·§6) | 9/16 |
| D-26 `(제안)` | 무효화 트랜잭션에서 영향 게시물의 진행 중 job `CANCELLED`(§8) | 9/16 |
| D-24 보류 중 삭제 | 게이트로 SENTENCE 를 보류하는 동안 게시물이 삭제되면 SENTENCE 를 넣는가(가짜 백엔드는 PREPARE `CANCELLED` 로 대기 해제 뒤 INSERT) | 9/16 |
| D-26 탈퇴 범위 | 작성자 탈퇴 시 그 사용자 게시물 전부의 job 을 끄는 방식(가짜 백엔드에 재현 안 함) | 9/16 |

## 15. 9/8 결정 기록 — 서버 코드 대조 (`geoji-server` · `geoji-web` 확인)

> 9/8 AI 파트가 `geoji-server`(Spring Boot 4.1.1 · Java 25 · Supabase Postgres · Elastic Beanstalk t3.micro)와 `geoji-web`(`docs/product/SPEC.md` · `src/shared/domain/*.ts`)을 읽고 사용자와 확정한 것. **§14 의 D-20 · D-21 · enum 매핑 행은 이 표가 대체한다.** 계약서 01 의 enum 교체는 CT-07 에서.

### 15.1 서버 현재 상태 (9/8 `main` 7550b1e 기준, 사실)
- **재판 흐름이 없다.** 게시물·투표·평결·양형·배심 테이블과 API 가 하나도 없고 `rooms.vote_deadline_minutes` 만 있다 → §2 `004` · §3 INSERT · §4 내부 API · §5 finalize · §6 watchdog · §9 공개 API 가 **전부 신규 작업**
  - (9/14 코드 대조: 9/11 서버 `d0f9fd5` 에 재판 API 는 생겼으나 `/internal/v1/*`·스냅샷 필드는 없음 — §14 CaseSnapshot 행)
- 있는 것: `profiles`, `rooms`(`spice_level` = `mild/hot/direct`, `rules text[]`), `room_members`(`debt_score`), `expenses`(`category` 자유 문자열, "AI 추론 자리"), `comments`(expense 단위), `crown_history`, `weekly_awards`, `challenges`, `patrol_notifications`, `daily_logs`
- `ai/AiClient.java` — 상 이름 발명 · 도전 과제 서술 · 순찰 위험 문구 3개를 **동기 HTTP** 로 기대하는 인터페이스. 지금은 `StubAiClient` 고정 문구
- DB: **Supabase 관리형 Postgres 1개**, Session Pooler(IPv4, `aws-0-ap-southeast-1.pooler.supabase.com:5432`) 접속. 스키마 소스는 `supabase/migrations/0001_init.sql`(별도 저장소, 여기 없음), Hibernate `ddl-auto: validate`. enum 은 Postgres native enum, 소문자
- 배포: Elastic Beanstalk Java SE, t3.micro 단일 인스턴스, `main` push 시 GitHub Actions 자동 배포. 댓글 실시간은 Supabase Realtime
- 프론트(`geoji-web`): 재판 흐름 그대로. `Intensity = mild|spicy|hell`, `Verdict = guilty|notGuilty|agree|disagree|dismissed`, `PostType = spent|considering`, `Sentence = probation|oneDay|life`, 카테고리 11종 상수. `ROADMAP.md` 미결정 표에 "판결 전달 방식(동기 또는 SSE) — AI·백엔드가 정한다" 가 남아 있다

### 15.2 확정 (9/8, 사용자)
| ID | 결정 | 백엔드가 할 일 | AI 파트가 할 일 |
|---|---|---|---|
| 범위 | **재판 흐름(proposal2)이 팀 확정 방향.** 서버의 시상식·왕관·순찰·도전·하루로그는 별개 기능 | §2~§9 그대로 착수 | — |
| **D-20** | 공유 Postgres = **Supabase 인스턴스 그대로.** `ai` 스키마를 같은 DB 에 둔다. "업무 트랜잭션 안 INSERT" 는 평결 확정 트랜잭션(§3)이 생기면서 같이 | `ai_api`·`ai_worker` role 생성·grants(§1), Session Pooler 접속 정보 전달. (9/14 코드 대조: `backend` role 도 001~003 `GRANT` 대상이라 같이 만든다) | 001~003 러너를 Supabase 에 Session Pooler 로 적용 |
| 연동 | **하이브리드.** 판결(그래프 B·C·retain·TEXT_RETRY)은 `ai.jobs` 큐 + 워커 + finalize(이 문서). 서버 `AiClient` 3종(상·도전·순찰)은 **동기 HTTP** 로 별도 제공 — P0 범위 밖, M3 이후 여유 시 | `AiClient` HTTP 구현은 AI 파트 API 문서가 나온 뒤 | 동기 엔드포인트 3개는 별도 카드(09 P1 후보) |
| **D-21** | **프론트 값이 API 표준.** 강도 `mild/spicy/hell`, 평결 `guilty/notGuilty/agree/disagree/dismissed`, 게시물 `spent/considering`, 형량 `probation/oneDay/life`. 짤 태그 5종은 기획서 값(`GUILTY_HEAVY`…) 유지 | `rooms.spice_level` enum 을 `mild/spicy/hell` 로 변경(`hot→spicy`, `direct→hell`). 평결·게시물·형량 enum 도 위 값으로 | 01 계약 enum 전면 교체(CT-07, 0.25d) |
| 카테고리 | **프론트 11종 고정**: 식비 · 배달 · 카페/간식 · 교통/택시 · 쇼핑/패션 · 뷰티 · 취미/여가 · 술/유흥 · 구독 · 생활 · 기타 | `posts.category` CHECK 제약(`expenses` 도 맞추면 좋음) | 심문관 enum 주입 값 = 이 11종(07 §3.3) |
| 등록 필드 | **프론트 DESIGN-SPEC S-09 에 맞춤(9/8):** 무엇을 `item` 필수 ≤ 30자, 사유 `reason` 선택 ≤ 200자. 심문(솔직 팝업 S-09b)은 무엇을만 본다. 판결문 본문 `statement` 합산 ≤ 300자(프론트 SPEC) | `posts.item NOT NULL`, `reason` NULL 허용, `verdict_texts.statement` 300자 | 01·07 계약, 05 검증 규칙 |
| 모델 | **계획서대로 Grok(서기) + OpenAI luna(심문관·양형관·검수관).** 프론트 `SPEC.md` "Claude Haiku 4.5 단독" · `ROADMAP.md` "Grok 은 심사 이후" 는 구버전 | — | 프론트 문서 갱신 요청, 제출 폼 "사용한 AI 도구명" 갱신 |
| **전달** | **폴링.** Supabase Realtime · SSE 는 안 한다. 프론트가 `GET /posts/{id}/verdict` 를 1초 간격으로, **화면 이탈 시 중단, `text_status=AI_READY` 면 즉시 중단**(`TEMPLATE_READY` 는 §9 의 30초 규칙). Realtime 은 M4 이후 검토하되 그때도 폴링을 재연결 폴백으로 남긴다 | §9 `GET verdict` 그대로. **Realtime publication · RLS 정책 작업 없음** | 프론트 `ROADMAP.md` 미결정 행 닫도록 통보 |
| 배포 | **백엔드가 관리하는 별도 EC2 1대에 Docker Compose.** AI 파트는 `ai-api` · `ai-worker` 이미지(GHCR) + compose 조각 + 환경변수 목록만 넘긴다 | EC2 생성, compose 실행, 환경변수·벤더 키 주입(§16.1), Supabase 접속 | Dockerfile 2개 · CI 이미지 빌드 · compose 조각(02). (9/14 코드 대조: Dockerfile·운영 compose 조각·이미지 태그는 아직 AI 저장소에 없다. `docker-compose.dev.yml` 은 로컬 Postgres 전용) |

### 15.3 확정 (9/8, 사용자 — AI 파트 내부 결정, 백엔드는 참고만)
| ID | 결정 | 백엔드에 걸리는 것 |
|---|---|---|
| D-08 | AI 파트 **2명**, 일정 그대로(07 보류 없음, M3 9/15 유지) | 없음 |
| D-07 | `GUARDRAIL_POLICY_VERSION=guardrail-v2` 로 시작. 팀 비준은 M3 검수(9/15) 때, 미비준 시 v1 | finalize 가 저장하는 정책 버전 문자열이 `guardrail-v2`. (9/14 코드 대조: `APP_ENV=production` 이면 환경에 **명시하지 않으면 기동 실패**, §16.1) |
| D-19 | 양형 이유 템플릿 치환 **확정으로 닫음**(팀 확인 불필요) | §5 `reason_source=TEMPLATE` 그대로 |
| D-04 | 방 댓글 말투 예시는 **P0 제외**, `ROOM_COMMENT_STYLE_ENABLED=false` 유지. retain 은 한다 | `resolve-evidence` 의 `style_comments` 는 P0 에서 빈 배열 |
| D-22 | 모델 동시성 8 로 시작, 429 시 하향 | 없음 |
| 키·결제 | xAI · OpenAI 키는 **AI 파트 개인 계정**으로 발급·결제, 팀에 정산. 키는 EC2 환경변수로만 전달 | compose 환경변수에 `XAI_API_KEY` · `OPENAI_API_KEY` 주입, 로그 금지 |
| 알림·비용 | 알림은 **디스코드 웹훅**(우리 `ALERT_DISCORD_WEBHOOK_URL`), 비용 경고 **일 5,000원**. 감정 어휘 6종은 08 §3.4 초안 확정 | 웹훅 URL 공유(같은 채널에 헬스체크 알림), `meme_images.emotions` 값은 이 6종 |
| AiClient 3종 | 서버의 상 이름·도전 서술·순찰 문구용 동기 엔드포인트는 **P1(심사 이후)**. P0 에서는 `StubAiClient` 유지 | `AiClient` HTTP 구현 착수 시점도 P1 |

### 15.4 남은 것
- §14 의 나머지 행(CaseSnapshot 필드, §4.4 · §4.5 · §4.6, §5 10단계)은 그대로 회신 대기. 9/14 옛 백엔드 전달 파일의 회신 대기 행도 §14 로 합쳤다
- D-23(양형관 실측), 검수관 모델은 작업 3·6 실측 뒤. LangSmith·CI 실비는 P1

### 15.5 9/14 결정 기록 — 준비 자료(PREPARE)와 선고(SENTENCE)의 순서·폴백·삭제·부분 재사용

> 9/14 사용자 결정. 배경: 준비 자료(`ai.trial_prep` — 조서 + 드립 후보)가 없으면 판결 품질이 떨어지는 네 경우(아직 안 끝남·실패·무효·입력 변경)를 정리했다.

| ID | 결정 | 백엔드가 할 일 | AI 파트가 할 일 |
|---|---|---|---|
| **D-24** | **SENTENCE 는 PREPARE 가 끝난 뒤 시작한다.** 평결 확정 때 PREPARE 가 진행 중이면 최대 **30초** 기다린다. 판결문은 맨 나중에 나오므로 체감 비용이 작다. 마감 10초는 SENTENCE INSERT 시각부터 | §3 게이트·§6 마감 기준 변경(`(제안)`, §14) | 없음(워커는 SENTENCE 를 받은 시점 기준 그대로). 가짜 백엔드·통합 테스트에 게이트 반영 |
| **D-25** | **준비 자료 폴백 계단.** ① 조서+드립 → ② 조서만(`DOSSIER_READY`) → ③ PREPARE 실패로 게이트 해제 → ④ 즉석 조서(INLINE, 드립 생략) — **서기·검수·finalize 시간(서기 상한 + 검수 상한 + finalize 0.5초, 구현 `inline_context_reserve`. PR #33 이후 `reserve_after(SENTENCING)` 은 0)을 먼저 남기고 남는 시간이 있을 때만** → ⑤ 최소 조서(MINIMAL, 모델 호출 0) → ⑥ watchdog 템플릿 + TEXT_RETRY. 기본 상한(서기 6초·검수 4초)에서는 10초 마감 안에 ④ 가 들어가지 않아 ⑤ 로 간다 — 상한을 낮추면 자동으로 ④ 가 켜진다 | 없음 | 05 §3.3 `inline_context` 조건을 이 규칙으로(9/14 리뷰 결함(조서가 서기 시간을 먹음) 해소) |
| **D-26** | **게시물 삭제·공유 철회·탈퇴 시 진행 중 작업을 끈다.** 결과를 버리는 것(epoch 검사)에 더해, 모델 비용이 더 나가지 않게 한다 | §8 무효화 트랜잭션에서 영향 job `CANCELLED`(`(제안)`, §14) | 모델 호출 직전 epoch 확인 → 달라졌으면 호출 0·`EVIDENCE_INVALIDATED`. heartbeat 취소 경로 통합 테스트 |
| **D-27** | **입력이 바뀌면 바뀐 부분만 다시 만든다.** 준비 자료 유효 판정을 한 덩어리 `input_hash` 대신 부분 키로: 조서 = 게시물 필드·심문 결과·방 규칙 버전(epoch 은 키에 넣지 않고 dossier `privacy_versions` == 스냅샷 비교로 따로 본다), 드립 후보 = 조서 + 그 강도(강도별). 방 강도만 바뀌면 드립만, 규칙 버전이 바뀌면 조서부터. **epoch 가 바뀐 것(삭제·철회)은 부분 재사용하지 않는다.** 모델 호출 단위 캐시(`node_results`)는 그대로. 조서 키에 메모리 recall 결과·`audience_version` 은 넣지 않는다 — recall 은 시간에 따라 바뀌어 넣으면 재사용이 거의 안 되고, 대가로 새 과거 기록이 생겨도 게시물·심문 결과·규칙 버전이 같으면 이전 조서를 쓴다(9/14 코드 대조) | 없음(방 강도·규칙 변경 때 PREPARE 재INSERT 는 기존 규약) | 05 §3.2 `input_hash`·§3.3 `load_valid_prep` 을 부분 키로. DDL 은 기존 컬럼(`dossiers.snapshot_hash`·`trial_prep.banter_json`)으로 먼저, 부족하면 결정 요청 |
| **9/14 거부 본문** | AI API 거부 응답 본문을 백엔드와 같은 `{"code"}` 로 통일. 스키마 위반은 422 `INVALID_REQUEST` | 없음(이미 `{"code"}`) | AI API 예외 핸들러 |

## 16. 배포 · 연동 (9/14, 옛 백엔드 전달 파일 §1~§3 흡수)

계약을 실제로 붙이고 띄울 때 필요한 것. 비밀값의 실제 값은 여기 적지 않는다(키 이름과 전달 경로만).

### 16.1 배포 환경변수 (EC2 compose 에 넣을 것)

`ai-api`·`ai-worker` 컨테이너 둘 다 같은 목록을 받는다. 키 이름 전체는 AI 저장소 `.env.example`, 기본값·설명은 AI 저장소 `README.md` 설정 표. 여기는 **직접 적어야 하는 것**만.

| 키 | 값 | 없으면 | 비고 |
|---|---|---|---|
| `APP_ENV` | `production` | 개발 모드로 뜬다(기동 검사 느슨) | 9/11 확정 |
| `GUARDRAIL_POLICY_VERSION` | `guardrail-v2` | **기동 실패.** production 은 이 값을 환경에 직접 적어야 한다(코드 기본값에 기대면 막힌다) | 01 §3.7 ①, §15.3 D-07. 팀 비준 뒤 `guardrail-v1` 로 내릴 수 있다 |
| `OPENAI_API_KEY` · `XAI_API_KEY` | AI 파트가 전달 | 뜨긴 하나 `/health/ready` 가 503. intake 는 폴백(§4) | §15.3 키·결제. 로그 금지 |
| `DATABASE_URL` | Supabase Session Pooler 접속 문자열(`ai_api`/`ai_worker` role) | `/health/ready` 503. AI API 는 제출 임시 예산(§4) 없이 돈다 | §2, D-20. `postgresql://` 그대로 줘도 된다(어댑터가 `+asyncpg` 로 바꾼다) |
| `BACKEND_INTERNAL_URL` | 백엔드 내부 API 베이스 URL | **워커 기동 실패**(비어 있으면) | §4 |
| `SERVICE_AUTH_TOKEN` | 내부 API 서비스 토큰(양쪽 같은 값) | AI API 는 전부 401(열어 두지 않음), 워커 → 백엔드도 401 | §4.7 |
| `ALERT_DISCORD_WEBHOOK_URL` | 팀 디스코드 웹훅 | 알림 없음 | §15.3 |

- 시간 예산 키 `INTAKE_TIMEOUT_SECONDS`(기본 4)·`SENTENCING_NODE_TIMEOUT_SECONDS`·`WRITER_NODE_TIMEOUT_SECONDS`·`EVALUATOR_NODE_TIMEOUT_SECONDS` 는 소수를 받는다(9/14, 기본값 불변). 운영에서는 적지 않는다
- 서기·드립 모델(`MODEL_WRITER`)에 추론 모델(`grok-4.6`·`4.5`·`4.3`)을 넣으면 환경과 무관하게 기동 실패

### 16.2 AI API 엔드포인트

| 경로 | 응답 | 용도 | 인증 |
|---|---|---|---|
| `GET /health/live` | 200 `{"status":"ok"}` | 프로세스 생존. compose healthcheck 에 쓴다 | 무인증 |
| `GET /health/ready` | 200 `{"status":"ok","missing":[]}` 또는 503 `{"status":"not_ready","missing":[키 이름]}` · 503 `{"status":"not_ready","db":"unreachable"}` | 키·DB 준비 여부. 503 이면 트래픽을 보내지 않는다 | 무인증 |
| `POST /internal/v1/intake` | `IntakeResult` | 심문관. 백엔드가 호출. 동작·폴백·422 는 §4 | 서비스 인증 |
| `GET /internal/v1/metrics/snapshot` | `{generated_at, db: ok|unreachable, metrics[]}`(지표마다 `source: process|db`) | 운영 지표 스냅샷 | 서비스 인증 |
| `GET /internal/v1/trials/{post_id}/trace` | 라벨·코드·개수·시각(원문 없음). 기록 없으면 404 `TRACE_NOT_FOUND` | 데모 C 내부 trace 화면. 프록시 주체는 §14 | 서비스 인증 |

- 워커 프로세스에는 HTTP 엔드포인트가 없다. 로컬 기동 포트는 README "실행 절차"(`uvicorn geoji_ai.api.app:app --port 8100`)

### 16.3 복사해 가거나 실행할 파일

| 파일·명령 | 어디에 | 왜 |
|---|---|---|
| `database/migrations/001_ai_jobs.sql`·`002_preparation_evidence.sql`·`003_memory_call_ledger.sql` | 적용만(복사 불필요) | `DATABASE_URL=<Session Pooler URL> uv run geoji-ai migrate` 로 AI 파트가 적용한다. 번호 순·`ai.schema_migrations` 기록·재적용 no-op·advisory lock 으로 동시 실행 안전. **001~003 만** 적용하고 004 는 백엔드 소유(§2). **파일에 `CREATE ROLE` 이 없다** — `ai_worker`·`ai_api`·`backend` role 을 먼저 만들어야 `GRANT` 가 통과한다. 권한 표는 §1 |
| `database/sql/invalidate_scope.sql` | 백엔드 무효화 스케줄러 | 삭제·공유 철회 뒤 파생 데이터 무효화(§8, 04 §3.5). 한 트랜잭션, 바인드 `:t`·`:id`·`:scope_key` |
| `src/geoji_ai/adapters/postgres_jobs.py` 의 `REAPER_SQL` | 백엔드 스케줄러, 5초 주기 | lease 만료 회수(§7 원문). 운영에서 워커 `--reaper` 는 끈다 |
| `contracts/fixtures/templates-v1.json` | 백엔드 저장소, 버전 고정 | watchdog·generation-failed 폴백 문구(§10). 토큰 `{n}`·`{m}`·`{sentence_label}` |
| `contracts/*-v1.schema.json` 7종 | 참조(복사본은 바뀔 때 갱신) | 계약 정본(`case-snapshot`·`evaluation`·`finalize`·`intake`·`sentencing`·`verdict-view`·`writer-draft`). enum 은 프론트 값. 서버의 대문자 enum 은 백엔드가 맞춘다(§15.2 D-21). 9/14 `meme_hints.emotion` enum 반영 |
| `geoji-ai ledger-sweep --older-than 24h` | 실행만(이미지 명령), 백엔드 스케줄러 또는 cron **하루 1회** | 결과 불명(`UNKNOWN`) 호출과 `--older-than` 보다 오래된 `RESERVED` 호출(취소·강제 종료로 남은 예약)의 예약액을 spent 로 확정한다. `DATABASE_URL` 필요(08 §3.2). 정리된 행은 `status='UNKNOWN'` 그대로 `actual_micro_usd = estimated_max_micro_usd` — 집계 시 `UNKNOWN ∧ actual NOT NULL` = 정리됨. 출력: 호출 건수·micro-USD·예산 키 수·그중 RESERVED 건수. 스케줄은 AI 파트가 만들지 않았다 |
| `scripts/seed_agent_db.py` | 실행만(AI 저장소) | 데모 시드(드립 예시·데모 C 메모리). 멱등. `uv run scripts/seed_agent_db.py [--banter-csv … --banter-candidates …] [--demo-user U --demo-room R --post-ids P1,P2 [--verdict-ids V1,V2] [--now ISO8601]]`(08 §3.5). `meme_catalog` 은 만들지 않는다 |
| `scripts/seed_memory_demo_c.py` | 실행만(AI 저장소) | 데모 C 메모리만 따로. 백엔드 시드가 만든 스타벅스 post 2개·verdict 2개 id 를 받아 `uv run scripts/seed_memory_demo_c.py --user U --room R --post-ids P1,P2 [--verdict-ids V1,V2] [--now ISO8601]`. 멱등. `--now` 는 백엔드 시드 기준 시각과 맞춘다(04 §3.6, §12) |
| `docs/runbook.md` | 참조 | 헬스·알림·비용 경고·롤백·동결·토큰 회전 절차(08 §3.6). 리허설 체크리스트 포함 |
| `ai-api`·`ai-worker` 이미지 · compose 조각 | EC2 | §15.2 배포. (9/14 코드 대조: 아직 AI 저장소에 없다. 생기면 이 행에 태그 규칙 `ai-YYYYMMDD-N`(runbook §6)과 파일 경로를 적는다) |
