-- 04 §3.1 DDL 원문(003). 그 뒤 grants 는 10 §1 role 규칙. DELETE 는 누구에게도 주지 않는다(삭제는 소프트).
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
-- grants (10 §1). UPDATE 에는 WHERE 가 컬럼을 읽도록 SELECT 를 같이 준다.
GRANT SELECT, INSERT, UPDATE ON ai.memory_facts, ai.processed_memory_events, ai.case_budgets, ai.llm_calls, ai.node_results TO ai_worker;
GRANT SELECT, UPDATE ON ai.memory_facts, ai.node_results TO backend;
GRANT SELECT, INSERT, UPDATE ON ai.case_budgets, ai.llm_calls TO ai_api;
