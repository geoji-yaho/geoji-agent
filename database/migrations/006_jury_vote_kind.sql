-- 18 §3.1 데모 AI 배심원(떼거지봇). `ai.jobs.kind` 에 `JURY_VOTE` 를 더한다.
-- 001 은 열 CHECK 라 제약 이름이 자동(`jobs_kind_check`)이지만 이름에 기대지 않는다 —
-- `ai.jobs` 의 CHECK 중 정의에 `kind` 가 든 것을 찾아 지우고 이름을 정해 다시 만든다.
-- 004·005 는 백엔드 소유·예약 번호라 비어 있고, 이 파일이 우리 쪽 마지막 번호다(00 §8.1).
-- 러너는 simple query 로 파일을 통째로 실행하므로 DO 블록을 쓸 수 있다.
DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'ai.jobs'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%kind%IN (%'
    LOOP
        EXECUTE format('ALTER TABLE ai.jobs DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END
$$;

ALTER TABLE ai.jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('PREPARE','SENTENCE','TEXT_RETRY','RETAIN','JURY_VOTE'));
