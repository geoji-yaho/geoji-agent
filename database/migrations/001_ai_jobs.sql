-- 02 §3.1 DDL 원문. 큐 테이블과 인덱스 2개, role 3종에 대한 grants.
-- role 생성(`CREATE ROLE`)은 여기 없다. Supabase 의 role 은 백엔드 몫이고(10 §1·§2),
-- 로컬 role 3개는 `tests/integration/conftest.py` 가 만든다.
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
