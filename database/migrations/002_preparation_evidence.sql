-- 04 §3.1 DDL 원문(002). 그 뒤 grants 는 10 §1 role 규칙. DELETE 는 누구에게도 주지 않는다(삭제는 소프트).
-- role 생성은 여기 없다. 로컬 role 은 tests/integration/conftest.py 가 만든다.
GRANT USAGE ON SCHEMA ai TO ai_api;
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
-- grants (10 §1). UPDATE 에는 WHERE 가 컬럼을 읽도록 SELECT 를 같이 준다.
GRANT SELECT, INSERT, UPDATE ON ai.trial_prep, ai.dossiers, ai.evidence, ai.evidence_sources, ai.banter_examples TO ai_worker;
GRANT SELECT, UPDATE ON ai.evidence, ai.dossiers, ai.trial_prep TO backend;
GRANT SELECT ON ai.evidence_sources TO backend;   -- 무효화 SQL 이 역조회한다
