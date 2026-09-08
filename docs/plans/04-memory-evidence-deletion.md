# 🛠️ [Tech Spec] 기술 명세서: 작업 4 — 메모리 · 근거(Evidence) 발급 · 권한 범위 · 삭제 epoch (9/11~9/12)

> 근거: proposal2 §2.2 결정 20·21·22, §3 #5(trial_prep 불변)·#6(F-label↔UUID)·#11(조회는 코드), §5.2(`recall_candidates → resolve_sources → build_db_evidence`), §6.4 Evidence, §7.3 테이블, §11.3 삭제·권한 변경, §12 기능 플래그(`ROOM_COMMENT_STYLE_ENABLED=false`), §16 MemoryPort, §20 작업 4 + 먼저 실패시킬 케이스(A방 전용 기록을 B방 공통 문구에 제공 · 현재 사건 포함 반복 집계 · 삭제 후 늦은 retain 부활).
> **선행 문서: 01·02·03**(가짜 백엔드의 `resolve-evidence`). 소비자는 **05**(조서·서기가 Evidence pack 을 쓴다)·**08**(삭제 리허설). proposal2 §20: "작업 4 를 M2 전에 끝내지 못하면 데모 시나리오 C 가 성립하지 않는다. **9/12 를 넘기지 않는다.**"
> 사건 본문은 우리 DB 에 없다. **recall 은 참조만 돌려주고, 본문은 백엔드 `resolve-evidence` 가 권한·원본 버전을 걸러 준다.** 권한 미검증 payload 를 모델에 넣지 않는다(결정 20).

## 1. 개요 및 구현 목표

### 목적:
- **근거 발급 파이프라인**: `recall_*`(참조 후보 ≤ 20) → 백엔드 `resolve-evidence`(권한·원본 버전 필터 + 집계) → `build_evidence`(코드가 문장·라벨·scope 부여) → `ai.dossiers` + `ai.evidence` + `label_map`(`F{n}` ↔ UUID). 모델 I/O 는 `F{n}` 만 본다(§3 #6)
- **MemoryPort Postgres 어댑터**: recall 2종·retain 2종(RETAIN job 핸들러, `processed_memory_events` 같은 트랜잭션)·delete 3종. 벡터 없음
- **권한 범위**: Evidence `scope{visibility, room_ids}` 로 방 전용 정보가 다른 방 문구에 새지 않게. 공유 카드는 `PUBLIC` 만
- **삭제 epoch**: 백엔드가 올린 `privacy_epochs` 와 스냅샷의 `privacy_versions` 를 비교해 stale 입력을 거부하고, 무효화 SQL(백엔드 스케줄러)로 파생 데이터를 폐기한다. **늦은 retain 이 삭제된 원본을 되살리지 않는다**
- 반복 집계 기본안: **현재 사건 제외, 사건 생성 시각 이전 30일, 같은 카테고리 확정 소비 건수.** "카테고리 교통비 건수를 택시 이용 횟수로 바꾸지 않는다"(§6.4)
- 데모 C: 같은 사용자 스타벅스 2건 → 3번째 등록 → `recall_user` 2건 → 조서 `PATTERN` → 서기가 라벨 인용

### 핵심 플로우:
```
PREPARE(작업 5) ─ load_case(snapshot) ─ recall_user/room (참조 ≤20, 우리 DB) ─ resolve-evidence (백엔드: 권한·버전 필터 + 집계 + 방 규칙 + 최근 판결 + 승인 댓글)
   ─ build_evidence (코드): F0 THIS_CASE · AGGREGATE(소진율·티어·무지출·반복 30일) · RULE(걸린 규칙) · PRIOR/MEM(해석된 원본) → pack ≤ 12
   ─ persist: ai.dossiers(label_map) · ai.evidence · ai.evidence_sources
RETAIN job (sentence.finalized / comment.approved) ─ snapshot(job) ─ 안전 필터(댓글) ─ INSERT memory_facts + processed_memory_events (같은 트랜잭션)
삭제·공유 변경 (백엔드) ─ privacy_epochs +1 (같은 트랜잭션에서 원본 비활성화) ─ 스케줄러: 무효화 SQL (evidence_sources → evidence/dossiers/trial_prep/text_evidence_refs/memory_facts)
워커 ─ persist·finalize 직전 epoch 재확인 → 불일치면 EVIDENCE_INVALIDATED
```

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | DDL `002_preparation_evidence.sql`·`003_memory_call_ledger.sql`(원장 테이블은 작업 6 이 쓴다) | High | S |
| 3.2 | `adapters/postgres_memory.py` — recall 2종(참조만)·retain 2종·delete 3종 + RETAIN 핸들러 `application/retain_memory.py` | Critical | M |
| 3.3 | `resolve-evidence` 요청·응답 계약 소비 + `application/build_evidence.py`(코드 사실·라벨·scope·집계) + `adapters/postgres_preparation.py`(dossier·evidence 저장, `label_map`) | Critical | M |
| 3.4 | `domain/visibility.py`(scope 비교) · `domain/aggregation.py`(반복 집계 규칙) | High | S |
| 3.5 | privacy epoch 검사 + 무효화 SQL(백엔드 스케줄러용) + 늦은 retain 방지 + `domain/comment_safety.py` | High | S |
| 3.6 | 데모 C 메모리 시드 + 격리·집계·삭제 테스트 | High | S |

### Out-of-Scope
- 조서 LLM(`analyze_reason`)·드립 후보·`trial_prep` 상태 전이 — 작업 5. 작업 4 는 코드 Evidence 와 저장까지
- Hindsight·reflect·`memory_summaries`(P1, 09). 방 댓글 말투 **사용**(`ROOM_COMMENT_STYLE_ENABLED=false` 기본 — D-04 결정 뒤 켠다). retain 은 한다
- 원본 삭제·epoch 증가·무효화 스케줄러 **실행**(백엔드, 10 §8). 우리는 검사와 SQL 을 제공한다

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 기한 |
|---|---|---|
| 백엔드 | `POST /internal/v1/ai-jobs/{job_id}/resolve-evidence` 구현(10 §4.2): 후보 참조를 현 권한·원본 버전으로 걸러 본문 반환 + 집계(소진율·티어·무지출·30일 반복) + 걸린 방 규칙(원문·버전) + 최근 30일 판결 + 승인 댓글(플래그 시) | 9/11 |
| 백엔드 | RETAIN job 용 snapshot 확장(10 §4.1 `(제안)`): `sentence.finalized` 면 `verdict_final{sentence, sentence_source, sentencing_reason, reason_source, applied_intensity, banter_strategy}`, `comment.approved` 면 `comment{comment_id, version, room_id, post_id, post_status, author_id, content, created_at}` | 9/11 |
| 백엔드 | 삭제·탈퇴·방 삭제·공유 철회 시 `ai.privacy_epochs` 증가 + 같은 트랜잭션 원본 비활성화 + 무효화 스케줄러(§3.5 SQL) | 9/12 |
| 백엔드 | `ai.privacy_epochs` 테이블(004) + `scope_key` 규약: `user:{id}`·`room:{id}`·`post:{id}` | 9/11 |

### 팀 결정 대기
- **D-04** 댓글 메모리 고지 — 결정 전까지 `ROOM_COMMENT_STYLE_ENABLED=false`(retain 은 하되 모델에 넣지 않는다)
- Evidence·dossier 보존 기간(초기안: 사건 삭제 시 즉시, 그 외 90일)

## 3. 기술 상세 설계 (Technical Design)

### 3.1 DDL (`database/migrations/002_*.sql`·`003_*.sql`, proposal2 §7.3·§7.4)
```sql
-- 002_preparation_evidence.sql
CREATE TABLE ai.trial_prep (id uuid PRIMARY KEY, post_id text NOT NULL, post_version int NOT NULL, audience_version int NOT NULL,
  rules_version text, privacy_versions jsonb NOT NULL, prompt_version text NOT NULL, input_hash text NOT NULL,
  status text NOT NULL CHECK (status IN ('DOSSIER_READY','COMPLETE','INVALIDATED')), dossier_id uuid, banter_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), invalidated_at timestamptz,
  UNIQUE (post_id, input_hash, prompt_version));                       -- 완료분 불변, 재처리는 새 행 (§3 #5)
CREATE TABLE ai.dossiers (id uuid PRIMARY KEY, post_id text NOT NULL, snapshot_hash text NOT NULL,
  label_map jsonb NOT NULL, privacy_versions jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), invalidated_at timestamptz);
CREATE TABLE ai.evidence (id uuid PRIMARY KEY, dossier_id uuid NOT NULL REFERENCES ai.dossiers(id), label text NOT NULL,
  epistemic_type text NOT NULL CHECK (epistemic_type IN ('DB_RECORD','USER_CLAIM','MODEL_INFERENCE')),
  fact_type text NOT NULL CHECK (fact_type IN ('SPEND','VERDICT','MITIGATION','RULE','AGGREGATE')),
  text text NOT NULL CHECK (char_length(text) <= 500), scope jsonb NOT NULL, aggregation jsonb, occurred_at timestamptz,
  invalidated_at timestamptz, UNIQUE (dossier_id, label));
CREATE TABLE ai.evidence_sources (evidence_id uuid NOT NULL REFERENCES ai.evidence(id), source_type text NOT NULL,
  source_id text NOT NULL, source_version bigint NOT NULL, PRIMARY KEY (evidence_id, source_type, source_id, source_version));
CREATE INDEX evidence_sources_lookup ON ai.evidence_sources (source_type, source_id);       -- 무효화 역조회
CREATE TABLE ai.banter_examples (id uuid PRIMARY KEY, category text, strategy text NOT NULL, intensity text NOT NULL,
  text text NOT NULL, approved boolean NOT NULL DEFAULT false, version int NOT NULL DEFAULT 1, created_at timestamptz DEFAULT now());
-- 003_memory_call_ledger.sql
CREATE TABLE ai.memory_facts (id uuid PRIMARY KEY, bank_type text NOT NULL, bank_id text NOT NULL, fact_type text NOT NULL,
  epistemic_type text NOT NULL, source_type text NOT NULL, source_id text NOT NULL, source_version bigint NOT NULL,
  payload jsonb NOT NULL, scope jsonb NOT NULL, occurred_at timestamptz NOT NULL, deleted_at timestamptz,
  UNIQUE (bank_type, bank_id, source_type, source_id, source_version, fact_type));
CREATE INDEX memory_facts_bank ON ai.memory_facts (bank_type, bank_id, occurred_at DESC) WHERE deleted_at IS NULL;
CREATE TABLE ai.processed_memory_events (event_id uuid PRIMARY KEY, processed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE ai.case_budgets (post_id text PRIMARY KEY, cap_micro_usd bigint NOT NULL, spent_micro_usd bigint NOT NULL DEFAULT 0,
  reserved_micro_usd bigint NOT NULL DEFAULT 0);
CREATE TABLE ai.llm_calls (id uuid PRIMARY KEY, post_id text, job_id uuid, generation_id uuid, node text NOT NULL, call_index int NOT NULL,
  vendor text NOT NULL, model_id text NOT NULL, request_hash text NOT NULL,
  status text NOT NULL CHECK (status IN ('RESERVED','SENT','COMPLETE','FAILED','UNKNOWN')),
  estimated_max_micro_usd bigint NOT NULL, actual_micro_usd bigint, cost_ticks bigint, prompt_tokens int, completion_tokens int,
  reasoning_tokens int, cached_tokens int, provider_request_id text, started_at timestamptz, finished_at timestamptz,
  UNIQUE (generation_id, node, call_index));                          -- call_index = 서기 강도 슬롯 (§3 #3)
CREATE TABLE ai.node_results (call_id uuid PRIMARY KEY REFERENCES ai.llm_calls(id), request_hash text NOT NULL, model_id text NOT NULL,
  prompt_version text NOT NULL, policy_version text NOT NULL, privacy_versions jsonb NOT NULL, validated_output jsonb NOT NULL,
  created_at timestamptz DEFAULT now(), expires_at timestamptz NOT NULL, invalidated_at timestamptz);
```
`ai.text_evidence_refs`·`ai.verdict_commit_records`·`ai.privacy_epochs` 는 백엔드 finalize·삭제가 쓰므로 **004(백엔드)** 에 있다(10 §2). 우리 role 은 `privacy_epochs` SELECT 만.

### 3.2 MemoryPort Postgres (`adapters/postgres_memory.py`, proposal2 §16)
| 메서드 | 동작 |
|---|---|
| `recall_user(user_id, category, before, limit=20)` | 뱅크 `user/{user_id}`, `deleted_at IS NULL`, `occurred_at < before`(사건 생성 시각), `fact_type ∈ SPEND|VERDICT|MITIGATION`. `score = 2·[category 일치] + 1·[사유 키워드 ILIKE] + recency(30일 안 1)`. 상위 `limit` 의 **참조**만: `{source_type, source_id, source_version, score, fact_type}`. 본문(payload) 은 돌려주지 않는다 |
| `recall_room(room_id, category)` | `rules_hit`(RULE_HIT 참조, 90일), `style_example_refs`(COMMENT 참조 ≤ `STYLE_EXAMPLE_LIMIT`, **플래그 켜졌을 때만**), `strictness{category_guilty_rate, n}`(VERDICT 사실 집계, n < 3 이면 null) |
| `retain_verdict(event_id, payload)` | `sentence.finalized` RETAIN 핸들러가 부른다. 행 6종(§3.3). `processed_memory_events(event_id)` INSERT 를 **같은 트랜잭션**에. 이미 처리된 event_id 면 0행 |
| `retain_comment(event_id, payload)` | `comment.approved` 핸들러. `comment_safety.filter` 통과분만 `room/{room_id}` COMMENT 사실. 스코프 `ROOMS [room_id]` |
| `delete_user / delete_room / delete_post` | `deleted_at = now()`(소프트). 백엔드 무효화 스케줄러가 부르는 SQL 과 같은 조건(§3.5). 물리 삭제는 P1 |
- 키워드: 공백 분리 → 2자 이상 → 조사 제거(은·는·이·가·을·를·에·에서·도·로·으로·서) → 불용어 제거 → 긴 순 3개. 형태소 분석 없음
- 뱅크 ID 는 서버가 만든다. API 로 `user_id`·`room_id` 를 받아 조회하는 엔드포인트를 노출하지 않는다(§9.2)

### 3.3 retain 행 규칙 (`application/retain_memory.py`)
| 이벤트 | 뱅크 | fact_type | payload | scope | source |
|---|---|---|---|---|---|
| `sentence.finalized` | `user/{author}` | `SPEND` | `{post_id, post_type, category, amount_krw, reason, spent_at}` | `ROOMS(audience.room_ids)` / `public_share_enabled` 면 `PUBLIC` | `POST/{post_id}/{post_version}` |
| 〃 | `user/{author}` | `VERDICT` | `{post_id, result, guilty_ratio, vote_counts, sentence, sentencing_reason, reason_source, banter_strategy, applied_intensity}` | 위와 동일 | `VERDICT/{verdict_id}/{verdict_version}` |
| 〃 | `user/{author}` | `MITIGATION` | `{post_id, text}` — 양형관 `mitigating` 라벨이 가리키는 Evidence 텍스트 | 동일 | 동일 |
| 〃 | `room/{room_i}` ∀ audience | `VERDICT` | `{post_id, author_id, category, result, guilty_ratio, intensity}` | `ROOMS [room_i]` | 동일 |
| 〃 | `room/{room_i}` | `RULE_HIT` | `{post_id, rule_text, rule_version, category}` — dossier RULE Evidence 마다 | `ROOMS [room_i]` | `RULE/{rule_id}/{rule_version}` |
| `comment.approved` | `room/{room_id}` | `COMMENT` | `{comment_id, post_id, author_id, content, created_at}` | `ROOMS [room_id]` | `COMMENT/{comment_id}/{version}` |
- 입력은 RETAIN job 의 snapshot(10 §4.1 확장 필드). 사유·댓글 원문은 job payload 가 아니라 snapshot 에서 받는다
- 템플릿 판결(`reason_source=TEMPLATE`)도 VERDICT 로 저장한다. 문구 재시도 성공은 새 사실을 만들지 않는다(proposal2 §8.1)

### 3.4 근거 발급 (`application/build_evidence.py`, proposal2 §6.4)
| 단계 | 내용 |
|---|---|
| 입력 | `CaseSnapshot` + `ResolveEvidenceResponse{sources[{source_type, source_id, source_version, payload, scope}], aggregates{burn_rate, tier, no_spend_days, repeat_same_category_30d, excludes_post_id}, room_rules[{room_id, rule_id, version, text}], recent_verdicts[], style_comments[]}` |
| `F0` THIS_CASE | 코드가 문장 생성: 금액(DB_RECORD)·카테고리·사유 인용(USER_CLAIM) + 환산 1개(고정 표: 지하철 1,400원·아메리카노 4,500원 등). `epistemic_type=USER_CLAIM`(사유 부분 때문에), `fact_type=SPEND`, scope = 사건 audience |
| AGGREGATE | 소진율·티어·무지출 일수 1문장 + 반복 1문장("최근 30일 같은 카테고리 확정 소비 N건"). `aggregation{rule_version, start_at, end_at, excludes_post_id}` 필수. **택시 횟수처럼 항목 단위 숫자는 만들지 않는다** |
| RULE | `room_rules` 중 카테고리 키워드 사전(작업 5 `dossier_rules`)에 걸린 것. scope `ROOMS [room_id]` — 그 방 강도 문구에만 쓸 수 있다 |
| PRIOR / MEM | `recent_verdicts`·`sources` 를 문장으로. `DB_RECORD`. scope 는 백엔드가 준 값 그대로 |
| 라벨·상한 | `F0` → AGGREGATE → RULE → PRIOR/MEM(score 순) 으로 `F1..`, 총 ≤ `EVIDENCE_PACK_LIMIT=12`. 모델 추론 사실(`MODEL_INFERENCE`, 작업 5)은 그 뒤 번호 |
| 저장 | `ai.dossiers(label_map, snapshot_hash, privacy_versions)` + `ai.evidence` + `ai.evidence_sources`. **한 트랜잭션.** 저장 직전 epoch 재확인(§3.5) |
| `visibility.py` | `usable(evidence, target_room_ids)`: `PUBLIC` → 항상, `ROOMS` → `target ⊆ evidence.room_ids`, `PRIVATE` → 생성용 pack 제외. 공유 카드 텍스트(작업 5 `PUBLIC` 문구)는 `PUBLIC` 근거만 인용 가능 |

### 3.5 삭제 epoch · 무효화 · 늦은 retain (proposal2 §11.3)
- 워커 검사: `SELECT scope_key, epoch FROM ai.privacy_epochs WHERE scope_key = ANY(:keys)` 를 (a) dossier 저장 직전 (b) finalize 직전(작업 5)에 실행. 스냅샷 `privacy_versions` 와 하나라도 다르면 **저장하지 않고** `EVIDENCE_INVALIDATED`
- 무효화 SQL(백엔드 스케줄러 실행, 우리가 제공):
```sql
WITH hit AS (SELECT evidence_id FROM ai.evidence_sources WHERE source_type = :t AND source_id = :id)
UPDATE ai.evidence SET invalidated_at = now() WHERE id IN (SELECT evidence_id FROM hit) AND invalidated_at IS NULL;
UPDATE ai.dossiers d SET invalidated_at = now() WHERE invalidated_at IS NULL AND EXISTS (SELECT 1 FROM ai.evidence e WHERE e.dossier_id = d.id AND e.invalidated_at IS NOT NULL);
UPDATE ai.trial_prep SET status = 'INVALIDATED', invalidated_at = now() WHERE dossier_id IN (SELECT id FROM ai.dossiers WHERE invalidated_at IS NOT NULL) AND invalidated_at IS NULL;
UPDATE ai.memory_facts SET deleted_at = now() WHERE deleted_at IS NULL AND source_type = :t AND source_id = :id;
UPDATE ai.node_results SET invalidated_at = now() WHERE invalidated_at IS NULL AND privacy_versions ? :scope_key;   -- 해당 scope 결과 재사용 금지
-- text_evidence_refs → verdict view 템플릿 전환은 백엔드(10 §8)
```
- 늦은 retain: RETAIN 핸들러는 (1) `processed_memory_events` (2) snapshot 이 `deleted`/404 면 skip (3) 스코프 epoch 가 job 생성 시점보다 크면 skip. 세 조건이 "삭제 후 늦은 retain 부활" 을 막는다
- `domain/comment_safety.py`: `post_status=JUDGED` 만, 21~200자, URL·전화·이메일 제외, `DEATH_WORDS` 제외, 방 강도별 욕 규칙(`mild`·`spicy` 는 `PROFANITY` 포함 제외, `hell` 은 허용 목록 밖 제외), 작성자=피고인 제외, `@` 호출 제외, 정규화 중복 제거

### 3.6 데모 C 시드 (`scripts/seed_memory_demo_c.py --user --room --post-ids a,b`)
- `user/{uuid}` SPEND+VERDICT 2건(스타벅스 6,100원 −10일·−4일, `guilty` 80%/100%), `room/{uuid}` VERDICT 2건. scope `ROOMS [room]`. `source_id` 는 백엔드 시드가 만든 실제 `post_id`(10 §12) — 그래야 `resolve-evidence` 가 본문을 돌려준다
- 확인: 3번째 스타벅스 사건 fixture 로 `recall_user` → 2건, `resolve-evidence`(가짜) → 본문, `build_evidence` → `PRIOR` 2개 + AGGREGATE 반복 2건

## 4. 완료 기준 (DoD)

### 4.1 정량 목표
| 지표 | 목표 | 측정 |
|---|---|---|
| 방 누출 | A방 전용 RULE/COMMENT 근거가 B방 문구 pack 에 포함 **0** | `test_visibility.py` |
| 반복 집계 | 현재 사건 제외·30일 창 — 경계 케이스(29일·31일·같은 날) 정확 | `test_aggregation.py` |
| 삭제 | 삭제 후 recall 0, 무효화 후 dossier·trial_prep·node_results `invalidated`, 늦은 RETAIN skip | `test_deletion.py` |
| epoch | 스냅샷 epoch 불일치 → 저장 0행 + `EVIDENCE_INVALIDATED` | `test_epoch_guard.py` |
| label_map | 라벨 ↔ UUID 왕복, map 밖 라벨 → 근거 없음 처리 | `test_label_map.py` |
| recall 지연 | p95 ≤ 30ms(뱅크 ≤ 500행) | 100회 |
| 데모 C | 시드 → 3번째 사건에서 PRIOR 2 + 반복 2건 | 가짜 백엔드 통합 |

### 4.2 검증 테스트 시나리오
- **`test_visibility.py`** — [ ] `ROOMS[A]` 근거, target `{A,B}` → 제외 / target `{A}` → 포함 / `PUBLIC` → 항상 / `PRIVATE` → pack 제외 / 공유 카드 = `PUBLIC` 만
- **`test_aggregation.py`** — [ ] 현재 post 제외 / [ ] `before = created_at` 기준 30일 / [ ] 카테고리 일치만 / [ ] `aggregation` 메타 필수 / [ ] "택시 3회" 같은 항목 숫자 생성 안 함
- **`test_postgres_memory.py`** — [ ] recall 은 payload 없음 / [ ] score 정렬·limit 20 / [ ] retain 6행 + `processed_memory_events`, 같은 event 2회 → 0행 / [ ] UNIQUE 충돌은 무시 / [ ] 플래그 off 면 `style_example_refs=[]`
- **`test_deletion.py`** — [ ] 무효화 SQL 4문 / [ ] 늦은 RETAIN 3조건 / [ ] `delete_*` 소프트 삭제
- **`test_epoch_guard.py`** — [ ] dossier 저장 직전 불일치 → 롤백 / [ ] 일치 → 저장
- **`test_comment_safety.py`** — 8규칙 양·음성
- **통합(가짜 백엔드)**: 데모 C 시드 → `build_evidence` 결과 pack(F0·AGGREGATE·PRIOR×2) 스냅샷 테스트

### 4.3 동작 확인 가이드 (수동)
```bash
uv run geoji-ai migrate        # 001~003
uv run scripts/seed_memory_demo_c.py --user $U --room $R --post-ids $P1,$P2
psql "$DATABASE_URL" -c "select bank_type, bank_id, fact_type, payload->>'category', scope from ai.memory_facts order by occurred_at"
uv run python -m geoji_ai.application.build_evidence --snapshot contracts/fixtures/case-snapshot-starbucks-3rd.json --resolve tests/fakes/resolve_starbucks.json | jq '.facts[] | {label, fact_type, epistemic_type, scope}'
```

### 최종 완료 기준:
- [ ] 허용된 30일 이력만 집계되고, 삭제 직후 조회 차단·finalize 거부가 동작(proposal2 §20 작업 4 완료 기준)
- [ ] 방 누출 0·늦은 retain 0·epoch 불일치 저장 0 테스트 green
- [ ] 백엔드 `resolve-evidence`·snapshot 확장·`privacy_epochs` 가 9/12 까지 붙어 데모 C 경로가 가짜 없이 돈다
- [ ] D-04 결정 상태 기록, 플래그 기본값 유지

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| ME-01 | DDL 002·003 | 테이블 11개 + 인덱스 + 러너 등록 | db | 0.25d | 02 JQ-01 |
| ME-02 | MemoryPort 어댑터 | recall 2·retain 2·delete 3, 키워드, 트랜잭션·`processed_memory_events` | adapter | 0.5d | ME-01 |
| ME-03 | RETAIN 핸들러 | snapshot 확장 소비, 행 규칙 6종, 늦은 retain 3조건, dispatch 교체 | application | 0.5d | ME-02, 03 VF-02 |
| ME-04 | `build_evidence` + 저장 | F0·AGGREGATE·RULE·PRIOR/MEM, 라벨·상한, `label_map`, `postgres_preparation.py` | application | 0.75d | 03 VF-01(resolve fixture) |
| ME-05 | visibility·aggregation·comment_safety | domain 3모듈 + 테스트 | domain | 0.5d | 01 |
| ME-06 | epoch·무효화 | 검사 함수, SQL 4문, 백엔드 전달 | db | 0.25d | ME-04 |
| ME-07 | 데모 C 시드·통합 | 시드 스크립트, 가짜 백엔드 resolve fixture, pack 스냅샷 테스트 | qa | 0.25d | ME-04, ME-06 |

**ME-01** — [ ] 002 / [ ] 003 / [ ] grants(`ai_worker` R/W, `backend` `privacy_epochs`·`text_evidence_refs` 만) / [ ] 재적용 no-op
**ME-02** — [ ] recall SQL 2 + score / [ ] retain 트랜잭션 / [ ] delete 3 / [ ] 플래그
**ME-03** — [ ] 행 규칙 표 / [ ] 3조건 skip / [ ] 템플릿 판결도 VERDICT
**ME-04** — [ ] F0 문장·환산 표 / [ ] AGGREGATE 메타 / [ ] RULE 키워드 / [ ] PRIOR/MEM / [ ] 라벨·상한 / [ ] 저장 + epoch 검사
**ME-05** — [ ] `usable()` / [ ] 반복 규칙 / [ ] 댓글 8규칙
**ME-06** — [ ] 검사 함수 2지점 / [ ] SQL 4문 / [ ] 10 §8 전달
**ME-07** — [ ] 시드 / [ ] fixture / [ ] 스냅샷 테스트 / [ ] 데모 C 경로 기록
