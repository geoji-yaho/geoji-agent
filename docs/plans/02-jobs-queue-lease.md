# 🛠️ [Tech Spec] 기술 명세서: 작업 2 — 영속 큐 `ai.jobs` · claim · lease · heartbeat · 워커 런타임 (9/8~9/9)

> 근거: proposal2 §1(영속성의 기준은 Postgres), §2.2 결정 16·18·22, §7.2 기준 DDL, §8 이벤트·큐·lease(claim 쿼리·lease·재실행·슬롯), §14.1 설정, §14.2 async 주의, §19 실행 단위·role 분리, §20 작업 2 + 먼저 실패시킬 케이스.
> **선행 문서: 01**(ports·설정·`docker-compose.dev.yml`). **전제 D-20(9/8 확정)**: 백엔드와 같은 Supabase Postgres, 업무 트랜잭션 안에서 `ai.jobs` INSERT. INSERT 지점은 `10-backend-contract.md` §3, 이 문서는 **큐를 소비하는 쪽**만 다룬다.
> 외부 Redis·브로커 없음. LangGraph checkpointer 는 P0 비필수 — 재실행 단위는 job 이고 중간 결과는 `node_results`(작업 6)다.

## 1. 개요 및 구현 목표

### 목적:
- `ai.jobs` 가 **이벤트 기록 + 작업 큐**를 겸한다. 별도 outbox 를 두지 않는다(결정 16). 백엔드가 커밋한 job 을 워커가 `FOR UPDATE SKIP LOCKED` 로 집어 lease 15초 / heartbeat 5초 / **generation UUID** 로 소유권을 증명한다(결정 18)
- 워커는 업무 테이블(`verdicts`·`verdict_texts`·`votes`·`posts`)을 **직접 쓰지 않는다**(결정 17). 결과 저장은 백엔드 내부 API(작업 3·5)
- 먼저 실패시킬 것(§20 작업 2): **워커 둘이 같은 작업 claim · lease 만료 후 이전 owner 가 complete · max_attempts 초과 재claim** — 실제 Postgres 에서 claim 중복 0, 이전 generation 저장 0

### 핵심 플로우:
```
백엔드 트랜잭션 ──INSERT ai.jobs(kind, dedupe_key, aggregate_id/version, payload{참조만}, priority)──▶ COMMIT
ai-worker (슬롯 4: SENTENCE 2 · PREPARE 1 · BACKGROUND 1)
  loop 250ms+jitter ─ claim(kinds by slot) [SKIP LOCKED, lease 15s, generation_id 발급, attempts+1] ─ COMMIT
      └─ dispatch(kind) → application 핸들러 (작업 3 스텁 → 작업 5 실구현)
           ├─ heartbeat task 5s: UPDATE … WHERE id+owner+generation+RUNNING+lease_until>now()
           ├─ MODEL_CONCURRENCY_LIMIT(8) 세마포어 — 서기 병렬 호출 포함
           └─ complete / fail(retry_after) / release  — 전부 같은 소유 조건
backend scheduler (10 §7) ─ reaper: lease 만료 → PREPARE/RETAIN 은 attempts 안에서 QUEUED, SENTENCE 는 마감 전만, TEXT_RETRY 는 round 실패 처리
```
- **claim 트랜잭션을 commit 한 뒤에 모델을 부른다.** LLM·HTTP 대기 중 DB lock 을 잡지 않는다(§10.2)
- generation UUID 는 claim 마다 새로 만든다. 만료된 lease 를 이전 워커가 부활시키지 않는다(§8.3)

### 현상태
- 코드 없음. DDL 은 proposal2 §7.2 의 "설계 예시"를 그대로 채택하고 grants 만 더한다

## 2. 작업 범위 (Scope Boundary)

### In-Scope
| # | 항목 | 등급 | 난이도 |
|---|---|:--:|:--:|
| 3.1 | `database/migrations/001_ai_jobs.sql` — `ai` 스키마·`ai.jobs`·인덱스 2개·role 권한 | Critical | S |
| 3.2 | `adapters/postgres_jobs.py` — claim·heartbeat·complete·fail·release(소유 조건 공통) | Critical | M |
| 3.3 | `workers/main.py`·`dispatch.py`·`heartbeat.py` — 슬롯·폴링·취소 전파·종료 시 lease 반납 | Critical | M |
| 3.4 | dedupe_key·priority·max_attempts·kind 규약(백엔드와 공유) | High | S |
| 3.5 | reaper SQL(백엔드 스케줄러가 실행) + 개발용 `--reaper` 플래그 | High | S |
| 3.6 | 마이그레이션 러너 + 통합 테스트(워커 2 프로세스) | High | M |

### Out-of-Scope
- job INSERT(백엔드, 10 §3). 개발·테스트용 `scripts/enqueue_job.py` 만 둔다
- 핸들러 본체 — 작업 3(스텁)·작업 4(RETAIN)·작업 5(PREPARE·SENTENCE·TEXT_RETRY)
- deadline watchdog(백엔드, 10 §6). 워커는 마감을 넘긴 SENTENCE 를 claim 하지 않을 뿐이다(`deadline_at > now()`)
- LangGraph checkpointer, 외부 브로커

### 다른 파트에 요청 (백엔드·프론트)
| 대상 | 요청 | 기한 |
|---|---|---|
| 백엔드 | `001_ai_jobs.sql` 을 Supabase 에 적용할 주체·순서 합의(우리 러너가 Session Pooler 로 `ai` 스키마를 만들고 `ai_api`·`ai_worker` role·grants 는 백엔드가). 접속 정보 전달 | 9/9 |
| 백엔드 | job INSERT 5지점·dedupe_key·priority 표(§3.4) 채택. **업무 변경과 같은 트랜잭션** | 9/10 |
| 백엔드 | reaper·lease 회수를 백엔드 스케줄러에서 실행(§3.5 SQL 제공). 주기 5초 | 9/13 |
| 백엔드 | `ai_worker` role 은 `ai` 스키마만, `ai_api` role 은 심문용 최소 권한, `backend` role 은 업무 테이블 + finalize 대상 `ai` 테이블(`verdict_commit_records`·`text_evidence_refs`·`privacy_epochs`) | 9/10 |

### 팀 결정 대기
- 없음 — 9/8 확정(10 §15): D-20 Supabase 공유, D-22 동시성 8(429 시 하향), 배포는 **백엔드 관리 EC2 1대 + Docker Compose, 우리는 `ai-api`·`ai-worker` 이미지(GHCR) + compose 조각 + 환경변수 목록만**. Kubernetes 없음

## 3. 기술 상세 설계 (Technical Design)

### 3.1 DDL (`database/migrations/001_ai_jobs.sql`, proposal2 §7.2 그대로 + grants)
```sql
CREATE SCHEMA IF NOT EXISTS ai;
CREATE TABLE ai.jobs (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    event_type text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('PREPARE','SENTENCE','TEXT_RETRY','RETAIN')),
    dedupe_key text NOT NULL UNIQUE,
    aggregate_id text NOT NULL,
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
    schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    payload jsonb NOT NULL,                       -- 참조(ID·version)만. 사유·댓글 복제 금지
    status text NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
    priority integer NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL CHECK (max_attempts > 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    deadline_at timestamptz,
    lease_until timestamptz,
    owner_id text,
    generation_id uuid,
    last_error_code text,
    trace_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (attempts >= 0 AND attempts <= max_attempts),
    CHECK (status <> 'RUNNING' OR
           (owner_id IS NOT NULL AND generation_id IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX jobs_claim_idx ON ai.jobs (priority DESC, available_at, created_at) WHERE status = 'QUEUED';
CREATE INDEX jobs_lease_idx ON ai.jobs (lease_until) WHERE status = 'RUNNING';
-- grants (role 이름은 10 §2 와 맞춘다)
GRANT USAGE ON SCHEMA ai TO ai_worker, backend;
GRANT SELECT, UPDATE ON ai.jobs TO ai_worker;      -- INSERT 는 backend 만
GRANT INSERT, SELECT, UPDATE ON ai.jobs TO backend;
```
`payload` 예: PREPARE `{"post_id","post_version","audience_version"}`, SENTENCE `{"verdict_id","verdict_version","post_id"}`, TEXT_RETRY `{"verdict_id","verdict_version","round"}`, RETAIN `{"event":"sentence.finalized"|"comment.approved","verdict_id"|"comment_id","version"}`.

### 3.2 claim · heartbeat · complete (`adapters/postgres_jobs.py`, proposal2 §8.2·§8.3)
```sql
-- claim: 짧은 트랜잭션. 커밋 후 모델 호출
WITH next_job AS (
  SELECT id FROM ai.jobs
  WHERE status = 'QUEUED' AND kind = ANY(:kinds) AND attempts < max_attempts
    AND available_at <= now() AND (deadline_at IS NULL OR deadline_at > now())
  ORDER BY priority DESC, available_at, created_at, id
  LIMIT 1 FOR UPDATE SKIP LOCKED)
UPDATE ai.jobs j SET status='RUNNING', owner_id=:worker_id, generation_id=:generation_id,
  lease_until = now() + make_interval(secs => :lease_s), attempts = attempts + 1, updated_at = now()
FROM next_job WHERE j.id = next_job.id RETURNING j.*;
-- heartbeat / complete / fail / release 의 공통 소유 조건
WHERE id = :id AND owner_id = :worker_id AND generation_id = :generation_id
  AND status = 'RUNNING' AND lease_until > now()
```
| 동작 | SET | 비고 |
|---|---|---|
| heartbeat | `lease_until = now() + lease_s` | 갱신 실패(0행) → 핸들러 취소, 결과 저장 금지 |
| complete | `status='SUCCEEDED'` | finalize 200 이후에만 |
| fail | `status = CASE WHEN attempts >= max_attempts THEN 'FAILED' ELSE 'QUEUED' END, available_at = now() + retry_after, last_error_code, owner_id=NULL, generation_id=NULL, lease_until=NULL` | 오류 분류는 `domain/retries.py`(작업 6). 작업 2 는 코드 문자열만 |
| release(종료) | `status='QUEUED', attempts = attempts - 1, owner_id/generation_id/lease_until = NULL` | 정상 종료 시 lease 반납. attempts 를 되돌리는 이유: 실행하지 않은 시도를 세지 않기 위해 |

`SKIP LOCKED` 는 claim 에만 쓴다. `SQLAlchemy AsyncSession` 은 task 마다 별도(§14.2) — heartbeat task 와 핸들러 task 가 세션을 공유하지 않는다.

### 3.3 워커 런타임 (`workers/main.py`·`dispatch.py`·`heartbeat.py`)
| 항목 | 값 |
|---|---|
| 슬롯 | `SENTENCE=2`(foreground) · `PREPARE=1` · `BACKGROUND=1`(TEXT_RETRY·RETAIN). 슬롯마다 독립 claim loop, `kinds` 필터 |
| 폴링 | 빈 큐 `WORKER_POLL_MS=250` + jitter(0~100ms). claim 성공 시 즉시 다음 claim |
| 소유권 | `worker_id = <host>:<pid>:<slot>`, `generation_id = uuid4()` claim 마다 |
| heartbeat | 5초 task. 실패 시 핸들러 `Task.cancel()`. **취소는 상위로 전파**하고 `finally` 에서 세마포어·세션 정리 |
| 동시성 | `asyncio.Semaphore(MODEL_CONCURRENCY_LIMIT=8)` 를 프로세스 전역으로 — 서기 병렬 호출(강도 수)도 이 세마포어를 지난다 |
| 종료 | SIGTERM → 새 claim 중단 → 진행 중 핸들러에 `shutdown_deadline`(10초) → 끝나지 않으면 취소 → `release` |
| dispatch | `kind → handler` 표. 작업 2 는 4 kind 모두 `NotImplementedHandler`(즉시 `fail(error_code="NOT_IMPLEMENTED", retry_after=60)`). 작업 3·4·5 가 교체 |
| 관측 | `queue_wait_seconds`(claim 시각 − available_at), `lease_expired_total`, `stale_finalize_total`(작업 5) — `telemetry/metrics.py`(작업 8). 메트릭 라벨에 개별 ID 금지 |

### 3.4 kind · dedupe_key · priority · 재시도 규약 (proposal2 §8.1·§8.3, 백엔드와 공유)
| 업무 트랜잭션(백엔드) | kind / event_type | dedupe_key | priority | max_attempts | deadline_at |
|---|---|---|---:|---:|---|
| 게시물 저장 | `PREPARE` / `post.created` | `prepare:{post_id}:{post_version}:{audience_version}` | 30 | 2 | null |
| 배심원 평결 확정 | `SENTENCE` / `verdict.confirmed` | `sentence:{verdict_id}:{verdict_version}` | 100 | 2 | `confirmed_at + 10s` |
| 판결 최초 저장(finalize·watchdog) | `RETAIN` / `sentence.finalized` | `retain:verdict:{verdict_id}:{verdict_version}` | 10 | 5 | null |
| 템플릿 저장·재시도 필요 | `TEXT_RETRY` / `verdict.text_retry` | `text-retry:{verdict_id}:{verdict_version}:{round}` | 50 | 1 | round 시작 + 20s |
| 승인된 댓글 | `RETAIN` / `comment.approved` | `retain:comment:{comment_id}:{comment_version}` | 10 | 5 | null |

- `verdict.confirmed` 는 배심원 평결 확정만 뜻한다. AI 형량 확정은 `sentence.finalized`
- 재실행: PREPARE/RETAIN 은 attempts 안에서 `QUEUED`. SENTENCE 는 **마감 전만**(마감 후는 watchdog). TEXT_RETRY 는 round 당 1회, 실패는 다음 round(백엔드 스케줄러가 예약)
- **DB side effect 는 중복을 막지만 외부 LLM 호출은 장애 구간에서 반복될 수 있다.** 완료한 노드 결과가 있으면 `request_hash`·버전으로 재사용(작업 6 `node_results`)

### 3.5 reaper (백엔드 스케줄러 실행, SQL 은 우리가 제공)
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
- 개발·로컬: `uv run geoji-ai worker --reaper` 플래그로 같은 SQL 을 워커가 5초마다 실행(운영에서는 끈다)
- TEXT_RETRY `FAILED` 뒤 다음 round 예약과 SENTENCE `CANCELLED` 뒤 폴백은 백엔드 watchdog·스케줄러(10 §6·§7)

### 3.6 마이그레이션 러너·테스트 인프라
- `uv run geoji-ai migrate` — `database/migrations/NNN_*.sql` 을 번호 순 적용, `ai.schema_migrations(version, applied_at)` 기록. 004 는 백엔드 소유라 **우리 러너는 001~003 만** 적용한다(004 파일은 `10-backend-contract.md` §2 초안)
- `tests/integration/conftest.py`: `docker-compose.dev.yml` 의 `postgres:16`, 테스트마다 `ai` 스키마 재생성
- `scripts/enqueue_job.py --kind SENTENCE --verdict v1 --version 1` — 백엔드 없이 job 을 넣는다(dedupe_key 규약 §3.4 사용)

## 4. 완료 기준 (DoD)

### 4.1 정량 목표
| 지표 | 목표 | 측정 |
|---|---|---|
| claim 중복 | 워커 2 프로세스 × 슬롯 4, job 200개 → 같은 job 을 두 워커가 RUNNING 으로 잡은 횟수 **0** | `tests/integration/test_claim_contention.py` |
| stale 저장 | lease 만료 후 이전 owner 의 `complete`·`heartbeat` 성공 **0** | `test_lease_ownership.py` |
| max_attempts | 초과 job 이 claim 되지 않음, `FAILED` 전환 | 테스트 |
| claim 지연 | job 삽입 → claim p95 ≤ 350ms(폴링 250ms + jitter) | 로컬 100회 |
| 종료 | SIGTERM 후 RUNNING 잔여 0(release 또는 완료) | 테스트 |

### 4.2 검증 테스트 시나리오 (실제 Postgres)
- **`test_claim_contention.py`** — [ ] 두 프로세스가 동시에 claim → 각 job 은 정확히 한 generation / [ ] priority DESC·available_at 순서 / [ ] `deadline_at <= now()` 인 SENTENCE 는 claim 되지 않음
- **`test_lease_ownership.py`** — [ ] lease 만료(시계 조작 대신 `lease_s=1`) 후 이전 owner 의 heartbeat → 0행 / [ ] reaper 가 `QUEUED` 로 회수(PREPARE) / [ ] 회수 뒤 새 claim 은 새 generation / [ ] 이전 generation 의 `complete` → 0행
- **`test_retry_semantics.py`** — [ ] `fail(retry_after=5)` → `available_at` 5초 뒤, attempts 유지 / [ ] `max_attempts` 도달 → `FAILED` / [ ] TEXT_RETRY 는 reaper 에서 `FAILED` / [ ] release 는 attempts −1
- **`test_worker_runtime.py`** — [ ] 슬롯별 kinds 필터 / [ ] 세마포어 8 초과 시 대기 / [ ] heartbeat 실패 → 핸들러 취소 전파(`CancelledError` 상위 도달) / [ ] SIGTERM 정리
- **`test_migrations.py`** — [ ] 001 적용·재적용 no-op, `ai_worker` 로 INSERT 시 권한 오류

### 4.3 동작 확인 가이드 (수동)
```bash
docker compose -f docker-compose.dev.yml up -d postgres && uv run geoji-ai migrate
uv run geoji-ai worker --reaper &          # 로컬 1개
uv run scripts/enqueue_job.py --kind PREPARE --post p1 --version 1 --audience 1
psql "$DATABASE_URL" -c "select kind,status,attempts,owner_id,lease_until,last_error_code from ai.jobs order by created_at desc limit 5"
```

### 최종 완료 기준:
- [ ] 실제 Postgres 에서 **claim 중복 0, 이전 generation 저장 0**(proposal2 §20 작업 2 완료 기준)
- [ ] `001_ai_jobs.sql`·role 권한이 Supabase 에 적용됨(백엔드 grants 회신 후)
- [ ] 백엔드가 §3.4 규약으로 5지점 INSERT 를 구현하기로 함(10 §3)
- [ ] 워커가 4 kind 를 claim 하고 스텁으로 `fail(NOT_IMPLEMENTED)` 처리 — 작업 3 이 교체할 준비

## 5. 작업 분할 (Task Breakdown — 카드 연동)

| # | 카드명 | 설명 | 라벨 | 예상 | 선행 |
|---|---|---|---|:--:|---|
| JQ-01 | DDL·grants·러너 | `001_ai_jobs.sql`, `schema_migrations`, `migrate` CLI, compose | db | 0.25d | 01 CT-01 |
| JQ-02 | `postgres_jobs.py` | claim·heartbeat·complete·fail·release, 소유 조건 공통화, 세션 분리 | adapter | 0.5d | JQ-01 |
| JQ-03 | 워커 런타임 | 슬롯·폴링·generation·heartbeat task·취소 전파·세마포어·SIGTERM | worker | 0.5d | JQ-02 |
| JQ-04 | dispatch·규약 | kind→핸들러 표, `NotImplementedHandler`, dedupe/priority 상수, `enqueue_job.py` | worker | 0.25d | JQ-03 |
| JQ-05 | reaper | SQL + `--reaper` 플래그 + 백엔드 전달 | db | 0.25d | JQ-02 |
| JQ-06 | 통합 테스트 | 워커 2 프로세스 경합·lease·재시도·종료 | test | 0.5d | JQ-03, JQ-05 |

**JQ-01** — [ ] DDL / [ ] grants / [ ] 러너(001~003 만) / [ ] compose
**JQ-02** — [ ] claim CTE / [ ] 소유 조건 4동작 / [ ] `AsyncSession` per task
**JQ-03** — [ ] 슬롯 loop 3종 / [ ] `worker_id`·generation / [ ] heartbeat·취소 / [ ] 세마포어 / [ ] SIGTERM → release
**JQ-04** — [ ] dispatch 표 / [ ] 스텁 핸들러 / [ ] 규약 상수·스크립트
**JQ-05** — [ ] reaper SQL / [ ] 플래그 / [ ] 10 §7 에 전달
**JQ-06** — [ ] 5 테스트 파일 / [ ] 4.1 수치 기록
