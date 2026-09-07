# 🤝 [Contract] 백엔드 계약서 — 공유 Postgres · `ai.jobs` INSERT · 내부 API 5개 · finalize · watchdog · 재시도 · 삭제 epoch · 공개 API · 시드

> 근거: proposal2 §1(아키텍처), §2.2 결정 16~24, §7.1 업무 테이블 추가 필드, §8.1 이벤트·큐, §9 API 계약, §10 판결 확정 트랜잭션, §11 마감·재시도·삭제 경합, §12(정책 버전이 finalize 에 걸림), §17 짤, §19 role 분리, §20 작업 3·8 완료 기준·먼저 실패시킬 케이스, §21 백엔드·프론트 변경 계약. proposal2 는 git 에 없으므로 백엔드가 **이 파일 한 장만 읽으면** 되게 옮겨 적었다.
> **대상 독자: 백엔드 담당.** AI 파트 계획서(01~08)는 이 문서를 참조한다. 계약이 바뀌면 여기와 `01-contracts-fake-provider.md` 를 같이 고친다.
> **가장 먼저 답해야 할 것: D-20.** 백엔드와 AI 가 같은 Postgres 인스턴스를 쓰고, 업무 트랜잭션 안에서 `ai.jobs` 를 INSERT 할 수 있는가. 아니면 outbox 전달 계층이 먼저 필요하고 9/8~9/18 일정 전체가 흔들린다(proposal2 부록 D). **9/8 까지 회신.**

## 0. 읽는 법

| 절 | 내용 | 필요 시점 |
|---|---|---|
| §1 | 전제·아키텍처·role | 9/8 |
| §2 | DB — `ai` 스키마 권한, 마이그레이션 `004`(업무 테이블 변경) | 9/10 |
| §3 | 업무 트랜잭션에서 `ai.jobs` INSERT 5지점 | 9/10(2지점)·9/13(3지점) |
| §4 | 내부 API — snapshot · resolve-evidence · begin-generation · generation-failed(+오류 코드) · (제안) 매핑·trace | 9/10~9/13 |
| §5 | finalize 절차 12단계·결과 코드 | 9/13 |
| §6 | deadline watchdog | 9/10 |
| §7 | 재시도 round 스케줄러 · reaper | 9/13 |
| §8 | 삭제·권한 변경 — privacy epoch·무효화 | 9/12 |
| §9 | 공개 API — post-submissions · complete · verdict 폴링 · share-card | 9/10·9/14 |
| §10 | 템플릿 공유 파일 | 9/10 |
| §11 | 짤 점수 선택(finalize) | 9/17 |
| §12 | 시드 | 9/17 |
| §13 | 백엔드 수용 검사(먼저 실패시킬 케이스) | 각 시점 |
| §14 | 미결 | — |

## 1. 전제 · 아키텍처 · role (proposal2 §1·§19)

```
Frontend ──▶ Main Backend ── 내부 HTTP ──▶ AI API (FastAPI)  : 심문(그래프 A) 만
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
- role 분리: `ai_api`(심문용 최소 권한 — DB 는 `ai.case_budgets`·`ai.llm_calls` 쓰기만), `ai_worker`(`ai` 스키마 읽기·쓰기, 단 `jobs` INSERT 불가), `backend`(업무 테이블 전부 + `ai.jobs` INSERT/UPDATE + `ai.privacy_epochs`·`ai.verdict_commit_records`·`ai.text_evidence_refs` 쓰기 + 무효화 SQL 대상 `ai.evidence`·`ai.dossiers`·`ai.trial_prep`·`ai.memory_facts`·`ai.node_results` UPDATE)
- 내부 API 인증: `Authorization: Bearer <SERVICE_AUTH_TOKEN>`. 요청에 `X-Trace-Id`·`X-Request-Id`·`X-Job-Id`·`X-Generation-Id`. 로그에 토큰 금지. **외부 사용자가 임의 `user_id`·`room_id` 로 조회하는 엔드포인트로 노출하지 않는다**
- 공통 규칙(§6.1): ID 는 문자열(신규 AI 테이블 UUID, 기존 업무 ID 는 opaque text). 금액 `amount_krw` 양의 정수. 시각 RFC3339 UTC(`timestamptz`), 화면에서만 KST. 알 수 없는 필드 거부, `schema_version=1`. 강도 enum `MILD|SPICY|HELL`(D-21), 평결 enum `GUILTY|NOT_GUILTY|APPROVED|REJECTED` — **백엔드 실제 enum 과의 매핑을 9/9 까지 고정**

## 2. DB (proposal2 §7.1)

`ai` 스키마·`ai.jobs`(001)·준비·근거·메모리·원장(002·003)은 AI 저장소 마이그레이션 러너가 만든다. 아래 **004 는 백엔드 소유**(업무 테이블 변경 + 백엔드가 쓰는 `ai` 테이블 3개). 파일 초안은 AI 저장소 `database/migrations/004_verdict_generation.sql` 에 두고 백엔드 저장소로 옮긴다.

| 테이블 | 추가 필드·제약 |
|---|---|
| `posts` | `version int`, `audience_version int`, `intake_status`(`PASS`/`UNCLARIFIED`), `intake_source`(`AI`/`FALLBACK`), `submission_id UNIQUE`, `deleted_at`. **`reason NOT NULL`** |
| `verdicts` | `post_id UNIQUE`, `verdict_version int`, `jury_result`, `policy_snapshot jsonb`(`allowed_sentences[{code,rank}]`, `fallback_sentence`, `reason_required`, `version`), `confirmed_at`, `deadline_at` |
| `verdicts` | `sentence_status`(`PENDING`/`FINAL`), `sentence`, `sentence_source`(`AI`/`RULE`), `sentencing_reason`, `reason_source`(`AI`/`TEMPLATE`) |
| `verdicts` | `text_status`(`PENDING`/`GENERATING`/`TEMPLATE_READY`/`AI_READY`), `text_version bigint DEFAULT 0`, `active_generation_id uuid NULL`, `active_job_id uuid NULL` |
| `verdicts` | `retry_round int DEFAULT 0`, `pending_retry_at`, `applied_intensity`, `meme_image_id` |
| `verdict_texts` | `verdict_id`, `intensity`, `headline`, `statement jsonb`(문장 배열 `{text, kind, evidence_labels}`), `source`(`AI`/`TEMPLATE`), `text_version`, `dossier_id`; **UNIQUE(verdict_id, intensity)** |
| `submissions` | `id`, `actor_id`, `status`(`NEW`/`NEEDS_INPUT`/`COMPLETED`/`BLOCKED`/`EXPIRED`), `payload_hash`, `question_shown bool`, `final_check_count int`, `post_id`, `expires_at`; UNIQUE(actor_id, id) |
| `meme_images` | `tag`(5종) + `strategies text[]`, `emotions text[]`, `keywords text[]`, `is_active` |
| `ai.privacy_epochs` | `scope_key text PRIMARY KEY`(`user:{id}`·`room:{id}`·`post:{id}`), `epoch bigint NOT NULL DEFAULT 0` |
| `ai.verdict_commit_records` | `generation_id uuid PRIMARY KEY`, `request_hash`, `verdict_id`, `text_version`, `committed_at` |
| `ai.text_evidence_refs` | `verdict_id`, `text_version`, `intensity`, `field_path`, `evidence_id uuid REFERENCES ai.evidence(id)` |

- `RETRYING` 상태는 두지 않는다. **재시도 중에도 `text_status=TEMPLATE_READY` 를 유지**해 지금 보여줄 템플릿이 사라지지 않게 한다
- 잠금 순서(전 코드 공유): **privacy scope 행(key 오름차순) → verdict 행 → job 행.** claim/heartbeat 는 job 행만 잠근다. **LLM·내부 HTTP 를 DB 트랜잭션 안에서 기다리지 않는다**

## 3. 업무 트랜잭션에서 `ai.jobs` INSERT (proposal2 §8.1)

| 업무 트랜잭션 | kind / event_type | dedupe_key | priority | max_attempts | deadline_at | payload |
|---|---|---|---:|---:|---|---|
| 게시물 저장(`SPENT`·`DEBATING` 만, `NO_SPEND` 제외) | `PREPARE` / `post.created` | `prepare:{post_id}:{post_version}:{audience_version}` | 30 | 2 | null | `{post_id, post_version, audience_version}` |
| 배심원 평결 확정(전원 투표 즉시 or 마감 스캔) | `SENTENCE` / `verdict.confirmed` | `sentence:{verdict_id}:{verdict_version}` | 100 | 2 | `confirmed_at + 10s` | `{verdict_id, verdict_version, post_id}` |
| 판결 최초 저장(finalize 또는 watchdog) | `RETAIN` / `sentence.finalized` | `retain:verdict:{verdict_id}:{verdict_version}` | 10 | 5 | null | `{event:"sentence.finalized", verdict_id, verdict_version}` |
| 템플릿 저장·재시도 필요 | `TEXT_RETRY` / `verdict.text_retry` | `text-retry:{verdict_id}:{verdict_version}:{round}` | 50 | 1 | round 시작 + 20s | `{verdict_id, verdict_version, round, intensities[]}` |
| 승인된 댓글(안전 검토 통과) | `RETAIN` / `comment.approved` | `retain:comment:{comment_id}:{comment_version}` | 10 | 5 | null | `{event:"comment.approved", comment_id, comment_version}` |

```sql
INSERT INTO ai.jobs (id, event_id, event_type, kind, dedupe_key, aggregate_id, aggregate_version, payload, priority, max_attempts, deadline_at, trace_id)
VALUES (gen_random_uuid(), gen_random_uuid(), 'verdict.confirmed', 'SENTENCE', 'sentence:' || :verdict_id || ':' || :verdict_version,
        :verdict_id, :verdict_version, jsonb_build_object('verdict_id', :verdict_id, 'verdict_version', :verdict_version, 'post_id', :post_id),
        100, 2, :confirmed_at + interval '10 seconds', :trace_id)
ON CONFLICT (dedupe_key) DO NOTHING;   -- 같은 업무 트랜잭션 안. commit 뒤 워커가 250ms 안에 집는다
```
- `payload` 에는 **참조(ID·version)만.** 사유·댓글을 작업마다 복제하지 않는다
- `DISMISSED`(정족수 미달 각하)는 **선고 작업을 만들지 않는다.** `REJECTED`(살까 말까 부결)는 만든다 — 양형관만 건너뛴다
- 허용 목록이 비었거나 `fallback_sentence` 가 목록에 없으면 **선고 작업을 만들지 않고** 정책 설정 오류를 알린다(§5.3). 생산 환경에 임의 형량의 묵시적 기본값은 없다

## 4. 내부 API (proposal2 §9.2, 서비스 인증 필수)

| API | 소유 | 요청 → 응답 |
|---|---|---|
| `POST /internal/v1/intake` | **AI API** | 백엔드가 호출. `{schema_version, submission_id, payload_hash, mode: INITIAL|FINAL_CHECK, post_type, amount_krw, category, reason}` → `IntakeResult{status, missing_information[], message, category_review, injection_detected, intake_source}`. 타임아웃 5초, 실패 = `FALLBACK` 등록 허용 |
| `GET /internal/v1/ai-jobs/{job_id}/snapshot` | 백엔드 | §4.1 |
| `POST /internal/v1/ai-jobs/{job_id}/resolve-evidence` | 백엔드 | §4.2 |
| `POST /internal/v1/verdicts/{id}/begin-generation` | 백엔드 | §4.3 |
| `POST /internal/v1/verdicts/{id}/finalize` | 백엔드 | §5 |
| `POST /internal/v1/verdicts/{id}/generation-failed` | 백엔드 | §4.6 |

### 4.1 snapshot
- 검증: job 존재 ∧ `RUNNING` ∧ 헤더 `X-Generation-Id` 일치 ∧ lease 유효. 아니면 409
- 응답 `CaseSnapshot`(01 §3.2): `post{id, author_id, post_version, reason, amount_krw, category, post_type, created_at}`, `audience{room_ids, audience_version, public_share_enabled}`, `privacy_versions[{scope_key, epoch}]`(관련 scope 전부: post·author·각 room), `room_snapshots[{room_id, intensity, rule_version}]`, `intake_result`, `jury`(SENTENCE·TEXT_RETRY 일 때: `verdict_id, verdict_version, result, vote_counts, guilty_ratio, confirmed_at, deadline_at, policy{…}, target_intensities, default_intensity`)
- **`(제안)` RETAIN job 확장**: `sentence.finalized` 면 `verdict_final{sentence, sentence_source, sentencing_reason, reason_source, applied_intensity, banter_strategy}`, `comment.approved` 면 `comment{comment_id, version, room_id, post_id, post_status, author_id, content, created_at}`. 삭제된 원본이면 404 — 워커는 skip

### 4.2 resolve-evidence
- 요청 `{candidates[{source_type, source_id, source_version, score}], include: ["rules","aggregates","recent_verdicts","style_comments"]}` (후보 ≤ 20)
- **자유 조회 API 가 아니다.** job 이 가리키는 사건의 작성자·대상 방·공개 정책에 맞는 것만 반환. 현재 권한·원본 버전으로 걸러 stale 후보는 제외
- 응답 `{sources[{source_type, source_id, source_version, payload, scope{visibility: PUBLIC|ROOMS|PRIVATE, room_ids[]}}], aggregates{burn_rate, tier, no_spend_days, repeat_same_category_30d, excludes_post_id, window{start_at, end_at}, rule_version}, room_rules[{room_id, rule_id, version, text}], recent_verdicts[{post_id, post_version, category, amount_krw, reason, result, sentence, judged_at, scope}], style_comments[{comment_id, room_id, content, created_at}]}` — `style_comments` 는 `ROOM_COMMENT_STYLE_ENABLED` 일 때만 채운다
- 반복 집계 규칙: 현재 사건 제외, 사건 생성 시각 이전 30일, 같은 카테고리 확정 소비 건수. 항목 단위(택시 횟수) 숫자는 만들지 않는다

### 4.3 begin-generation
- 요청 `{job_id, generation_id, verdict_version}`. 잠금 순서(§2)로 verdict·job 을 잠그고 lease·generation·평결 버전 확인 → `active_job_id`·`active_generation_id` 설정. 같은 현재 generation 재호출 허용. 다른 활성 작업이 유효하면 409
- SENTENCE 는 **마감 전 `PENDING`** 에서만, TEXT_RETRY 는 **템플릿이 이미 노출된 `FINAL`** 에서만 시작
- 응답 `{fixed_sentencing: {sentence, sentencing_reason, reason_source} | null, text_version, deadline_at}` — FINAL 이면 고정값을 준다(워커는 양형관을 부르지 않는다)

### 4.4 `(제안)` 제출 → 게시물 매핑 통지
- `POST /post-submissions/{id}/complete` 가 post 를 만들 때 AI API `POST /internal/v1/submissions/{id}/linked {post_id}` 를 호출하거나, PREPARE job payload 에 `submission_id` 를 넣는다. 심문 임시 예산을 사건 예산으로 이전하는 데 필요(06 §3.2). 없으면 제출 예산은 별도로 남는다

### 4.5 `(제안)` trace 조회
- 데모 C 관측 화면용. AI API `GET /internal/v1/trials/{post_id}/trace`(서비스 인증) 를 백엔드가 프록시하거나 내부망에서 프론트가 직접. 응답: dossier 라벨·recall 출처·노드 타임라인·비용(원문 없음)

### 4.6 generation-failed · 오류 코드 표 `(제안 — proposal2 에 코드 표 없음)`
- 요청 `{job_id, generation_id, error_code}`. 현재 세대만 처리, 다른 세대는 무시(409)
| error_code | 백엔드 처리 |
|---|---|
| `AI_NOT_READY`(M2 스텁) · `POLICY_ERROR` · `EVIDENCE_INVALIDATED` | 즉시 폴백(형량 `fallback_sentence` FINAL/RULE, 문구 TEMPLATE, RETAIN job). **TEXT_RETRY 예약 안 함** |
| `VENDOR_UNAVAILABLE` · `BUDGET_EXCEEDED` · `EVAL_FAILED` · `SCHEMA_INVALID` · `DEADLINE_EXCEEDED` | 같은 폴백 + `TEXT_RETRY` round 1 예약(§7) |
| TEXT_RETRY 중 실패 | 템플릿 유지, 다음 round 예약(남았으면), 마지막이면 운영 알림 |

## 5. finalize (proposal2 §10)

요청 `FinalizeRequest`(01 §3.2): `job_id`·`generation_id`·`verdict_version`·`expected_text_version`·`dossier_id`·`privacy_versions`·`draft_hash`·`sentencing{sentence, sentencing_reason, reason_source, evidence_labels, aggravating, mitigating} | null`·`draft{texts[{intensity, headline, statement, banter_strategy, selected_candidate_id, attack_angle, source}], meme_tag, meme_hints}`·`evaluation`·`evaluation_draft_hash`·`prompt_bundle_version`·`guardrail_policy_version`·`model_ids`.

**백엔드는 호출자가 보낸 평결·default intensity·허용 목록·`sentence_source` 를 신뢰하지 않는다. DB 스냅샷에서 읽는다.** 문구·검수 hash 일치, 근거 라벨→UUID(`ai.dossiers.label_map`), 길이(30/200/100), 강도 집합 = `target_intensities`, `guardrail_policy_version` = 설정값 같은 결정적 검증을 서버에서 다시 실행한다. 의미 검수는 AI 책임이지만 **보고서 누락·`false` 는 백엔드가 거부한다.**

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
| 결과 | HTTP |
|---|---|
| 저장 성공 · 동일 성공 재전송 | 200 `{verdict_id, text_version, committed_at}` |
| 다른 generation 활성 | 409 `STALE_GENERATION` — 워커는 결과 폐기 |
| Evidence 삭제·공개 범위 변경 | 409 `EVIDENCE_INVALIDATED` — 초안 폐기, 새 근거로 별도 작업 |
| 최초 노출 마감 초과 | 409 `DEADLINE_EXCEEDED` — watchdog 폴백 |
| 미확정 형량·schema·검수 오류 | 422 `INVALID_DRAFT` — 워커 보정 횟수 남으면 보정 |
| DB·네트워크 장애 | 워커가 같은 요청 재전송 → commit record. **모델 재호출 없음** |
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

## 7. 재시도 round · reaper (proposal2 §8.3·§11.2)

- round 1 은 템플릿 후 5분, round 2 는 10분, round 3 은 20분 뒤 `TEXT_RETRY` INSERT(§3 규약, `retry_round <= 3`, `(verdict_id, verdict_version, round)` 중복 방지). 스캔 주기 5분이라 최대 한 주기 추가 지연
- 최초 저장 형량·양형 이유 고정. 문구만 갱신. 성공 시 이미지·형량·이유 유지, `texts`·`source`·`text_version` 만 변경. 마지막 실패·비용 한도 초과는 템플릿 유지 + 운영 알림
- reaper(5초): `02-jobs-queue-lease.md` §3.5 SQL — lease 만료 회수. PREPARE/RETAIN 은 attempts 안에서 `QUEUED`, SENTENCE 는 마감 전만, TEXT_RETRY 는 `FAILED`(다음 round 예약)

## 8. 삭제 · 권한 변경 (proposal2 §11.3)

- 원본 삭제·댓글 삭제·작성자 탈퇴·방 공유 철회: **해당 scope 의 `ai.privacy_epochs` 를 먼저 잠그고 증가**, 같은 트랜잭션에서 원본 비활성화, 무효화 작업 기록. 판결 생성은 이전 epoch 로 저장할 수 없다(finalize 3 단계)
- **파생 정리는 비동기여도 읽기 차단은 즉시.** 판결 조회는 현재 epoch 와 저장 당시 epoch 를 비교, 불일치면 과거 문구 대신 공개 가능한 템플릿. 캐시·공유 카드 캐시도 버전 키가 달라지게
- 무효화 스케줄러: `04-memory-evidence-deletion.md` §3.5 SQL(evidence_sources → evidence/dossiers/trial_prep/memory_facts/node_results) + `text_evidence_refs` 로 영향받는 `verdict_texts` 를 템플릿으로 전환. 재시도는 삭제된 prep 를 읽지 않는다. **이미 `FINAL` 인 형량은 설명 삭제와 별개로 유지.** 사건 삭제 시 판결 조회도 차단
- 통합 테스트 대상: 다른 방 기록·댓글 삭제까지(scope 누락 시 epoch 검사가 무의미)

## 9. 공개 API (proposal2 §9.1·§9.3)

| API | 요청·응답 | 오류 |
|---|---|---|
| `POST /post-submissions` | 사유·금액·종류·카테고리·공유 방 → `{submission_id, status, intake_result}` 또는 `post_id`. 내부에서 `/internal/v1/intake(mode=INITIAL)` | 400 입력, 401, 403 공유 권한, 429 |
| `POST /post-submissions/{id}/complete` | `{action: REVISE|PROCEED, 최종 값, revision}`. `REVISE` 는 `/internal/v1/intake(mode=FINAL_CHECK)` 1회. **`BLOCKED` 를 `PROCEED` 로 우회 불가(409).** 중복 완료는 기존 post 반환 | 409 만료·버전 충돌·차단 |
| `GET /posts/{id}/verdict?room_id=` | `verdict-view-v1`: `jury_status, sentence_status, text_status, text_version, view|null, poll_after_ms`. 방 강도 행, 없으면 `applied_intensity`(최다 투표 방, 동률 방 생성일). 생성 중 200 + `view=null` | 404 없음/권한 없음 |
| `GET /posts/{id}/share-card` | 공개 허용 문구·이미지 metadata 만. **Evidence 원문·개인 이력 반환 금지.** `PUBLIC` 근거 문구만 | |

- 제출 상태 `NEW → NEEDS_INPUT → COMPLETED`, `BLOCKED`, `EXPIRED`. 최초 `PASS` 는 `NEW → COMPLETED`. `payload_hash` = 정규화된 타입·금액·사유·카테고리·공유 방 목록. 질문은 제출당 1회(`question_shown`)
- 프론트 폴링: 1초 → 15초 뒤 5초, 화면 이탈 시 취소, 템플릿 후 30초 또는 재진입. **`text_version` 이 작은 응답으로 UI 를 덮지 않는다.** 빈 화면 대신 대기 메시지
- `source=TEMPLATE` 이면 AI 판사 라벨·양형 이유 블록 숨김. 지옥맛 방장 확인 문구: "지옥맛은 반말과 욕설, 인격 조롱이 나옵니다. 멤버 전원이 동의했는지 확인해주세요."

## 10. 템플릿 공유 파일

`contracts/fixtures/templates-v1.json`(AI 저장소, 백엔드에 복사·버전 고정): 결과별 `{headline, statement[], sentencing_reason_template}`. 유죄 "배심원 {n}인 중 {m}인이 유죄로 판단했습니다. 형량: {형량 라벨}" / 무죄 "배심원단은 이 지출에 정상 참작의 여지가 있다고 판단했습니다." / 동의 "배심원단이 구매를 승인했습니다. 후회는 본인 몫입니다." / 기각 "배심원단이 구매를 기각했습니다. 지갑을 닫으십시오." 형량 라벨: `집행유예` / `징역 1일 (내일 하루 무지출)` / `무기징역 (3일 무지출)`. watchdog·generation-failed·부분 강도 템플릿 모두 이 파일.

## 11. 짤 점수 선택 (finalize 10 단계, proposal2 §17)

`meme_images` 중 `tag == draft.meme_tag ∧ is_active` → `+3` `banter_strategy ∈ strategies` · `+2` `meme_hints.emotion ∈ emotions` · `+1` `|keywords ∩ meme_hints.keywords|` · `−5` 같은 사용자 최근 노출 5장 → `crc32(post_id + image_id)` tie-break → `meme_image_id` 고정(새로고침 불변). 후보 0 → 결과별 기본 이미지. 감정 어휘 6종: `DISAPPROVAL` `ABSURD_SERIOUSNESS` `SMUG` `PITY` `CELEBRATION` `RESIGNATION`. 문구 합성은 클라이언트 캔버스, 이미지는 CORS 허용 CDN.

## 12. 시드 (9/17, proposal2 §18 데모 시드 생성기)

| 항목 | 수량 | 조건 |
|---|---:|---|
| 방 | 3 | 순한맛·매운맛·지옥맛 각 1, 마감 30분, 규칙 3~5개(택시·배달·커피 프리셋) |
| 사용자 | 4 | 데모 C 사용자 1명은 세 방 모두 |
| 게시물 | 12 | 카테고리 6 × 2. 판결 확정 10(유죄 6·무죄 2·동의 1·기각 1) + 투표 중 2. 데모 C 사용자 스타벅스 2건 → `post_id` 를 AI 파트에 전달(04 시드) |
| 댓글 | 20 | 판결 확정 글, 21자 이상, 방 강도 말투 |
| 판결 | 10 | **실제 파이프라인 통과**(PREPARE → 투표 → SENTENCE → finalize). ≈ 250원 |

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

| ID | 항목 | 기한 |
|---|---|---|
| **D-20** | 공유 Postgres 인스턴스 + 업무 트랜잭션 안 `ai.jobs` INSERT 가능 여부, DB 소유권·role 승인 | **9/8** |
| D-21 | 강도 enum `MILD|SPICY|HELL` 채택 | 9/9 |
| enum 매핑 | 평결 `GUILTY|NOT_GUILTY|APPROVED|REJECTED` ↔ 백엔드 실제 값, 카테고리 enum 목록 | 9/9 |
| CaseSnapshot | `post_version`·`audience_version`·`privacy_versions`·`rule_version`·`policy` 를 채울 수 있는가 | 9/9 |
| §4.4 | 제출 → 게시물 매핑 통지 방식 | 9/14 |
| §4.5 | trace 조회 프록시 주체 | 9/16 |
| §4.6 | `generation-failed` 오류 코드 표 채택 | 9/10 |
| §5 10단계 | 일부 강도 TEMPLATE 시 `TEXT_RETRY payload.intensities[]` 채택 | 9/13 |
