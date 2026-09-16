-- 04 §3.5 무효화 SQL. 백엔드 스케줄러가 한 트랜잭션으로 실행한다(10 §8). 우리가 제공하고 백엔드 저장소로 복사한다.
-- 바인드: :t = source_type, :id = source_id, :scope_key = privacy_epochs 의 scope_key.
-- node_results 조건은 원문 `privacy_versions ? :scope_key` 대신 `@>` 다. privacy_versions 는
-- [{scope_key, epoch}] 객체 배열이라 `?`(최상위 문자열 원소·키 검사)로는 매치되지 않는다(사용자 9/14 승인).
-- `:scope_key` 의 CAST 는 jsonb_build_object 가 variadic "any" 라 드라이버가 파라미터 타입을 정하지 못해서 붙였다.
WITH hit AS (SELECT evidence_id FROM ai.evidence_sources WHERE source_type = :t AND source_id = :id)
UPDATE ai.evidence SET invalidated_at = now() WHERE id IN (SELECT evidence_id FROM hit) AND invalidated_at IS NULL;
UPDATE ai.dossiers d SET invalidated_at = now() WHERE invalidated_at IS NULL AND EXISTS (SELECT 1 FROM ai.evidence e WHERE e.dossier_id = d.id AND e.invalidated_at IS NOT NULL);
UPDATE ai.trial_prep SET status = 'INVALIDATED', invalidated_at = now() WHERE dossier_id IN (SELECT id FROM ai.dossiers WHERE invalidated_at IS NOT NULL) AND invalidated_at IS NULL;
-- POST 삭제·철회는 같은 게시물에서 파생한 VERDICT·COMMENT·RULE_HIT 등도 정리한다.
UPDATE ai.memory_facts SET deleted_at = now() WHERE deleted_at IS NULL
  AND ((source_type = :t AND source_id = :id) OR (:t = 'POST' AND payload ->> 'post_id' = :id));
UPDATE ai.node_results SET invalidated_at = now() WHERE invalidated_at IS NULL AND privacy_versions @> jsonb_build_array(jsonb_build_object('scope_key', CAST(:scope_key AS text)));
-- text_evidence_refs → verdict view 템플릿 전환은 백엔드(10 §8)
