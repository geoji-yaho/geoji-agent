"""`ai.jobs` 큐 어댑터 (02 §3.2·§3.5).

`ports.jobs.JobsPort` 의 Postgres 구현. SQL 은 02 §3.2 claim CTE 와 §3.5 reaper 원문을 옮겼다.
`AsyncSession` 은 호출마다 새로 만든다(§3.2 마지막 줄 — heartbeat task 와 핸들러 task 가
세션을 공유하지 않는다). claim 은 짧은 트랜잭션이라 커밋한 뒤 `Job` 을 돌려준다.

업무 테이블은 읽지 않는다. 이 어댑터가 건드리는 것은 `ai.jobs` 뿐이다.
접속 문자열은 로그·예외 메시지 어디에도 싣지 않는다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.types import Text

from geoji_ai.contracts.jobs import Job

# --- SQL -----------------------------------------------------------------
# 02 §3.2 원문. `FOR UPDATE SKIP LOCKED` 는 claim 에만 쓴다.
CLAIM_SQL = """
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
"""

# 02 §3.2 원문. heartbeat / complete / fail / release 가 모두 이 조건을 쓴다.
OWNERSHIP_WHERE = """
WHERE id = :id AND owner_id = :worker_id AND generation_id = :generation_id
  AND status = 'RUNNING' AND lease_until > now()
"""

# 02 §3.2 동작별 SET 표. `now() + lease_s` 는 의사 표기라 claim 원문과 같은
# `make_interval(secs => ...)` 로 옮겼다(명세 대조 어긋남 ②). 의미는 그대로다.
#
# `updated_at` 은 §3.2 표에 없지만 claim 원문과 §3.5 reaper 원문이 둘 다 찍는다 —
# 쓰기 경로 전부가 찍는 것이 DDL(`updated_at ... NOT NULL DEFAULT now()`)의 의도다.
# 4동작만 빼면 `SUCCEEDED`·`FAILED` 로 끝난 행의 `updated_at` 이 claim 시각을 가리켜
# 백엔드 watchdog(10 §7)이 틀린 값을 본다. 한 조각으로 두고 넷이 붙인다.
_TOUCH = "updated_at = now(), "

HEARTBEAT_SQL = (
    "UPDATE ai.jobs SET "
    + _TOUCH
    + "lease_until = now() + make_interval(secs => :lease_s)"
    + OWNERSHIP_WHERE
)

COMPLETE_SQL = "UPDATE ai.jobs SET " + _TOUCH + "status='SUCCEEDED'" + OWNERSHIP_WHERE

FAIL_SQL = (
    "UPDATE ai.jobs SET "
    + _TOUCH
    + "status = CASE WHEN attempts >= max_attempts THEN 'FAILED' ELSE 'QUEUED' END, "
    "available_at = now() + make_interval(secs => :retry_after_s), "
    "last_error_code = :error_code, "
    "owner_id=NULL, generation_id=NULL, lease_until=NULL" + OWNERSHIP_WHERE
)

RELEASE_SQL = (
    "UPDATE ai.jobs SET " + _TOUCH + "status='QUEUED', attempts = attempts - 1, "
    "owner_id=NULL, generation_id=NULL, lease_until=NULL" + OWNERSHIP_WHERE
)

# 02 §3.5 원문 그대로. 한 글자도 바꾸지 않는다.
REAPER_SQL = """
-- 5초 주기. lease 만료 회수. SENTENCE 는 마감 전만 되살린다
UPDATE ai.jobs SET status = CASE
    WHEN kind = 'TEXT_RETRY' THEN 'FAILED'
    WHEN kind = 'SENTENCE' AND (deadline_at IS NULL OR deadline_at <= now()) THEN 'CANCELLED'
    WHEN attempts >= max_attempts THEN 'FAILED'
    ELSE 'QUEUED' END,
  owner_id = NULL, generation_id = NULL, lease_until = NULL,
  last_error_code = COALESCE(last_error_code, 'LEASE_EXPIRED'), updated_at = now()
WHERE status = 'RUNNING' AND lease_until < now();
"""

_ASYNCPG_SCHEME = "postgresql+asyncpg://"
_PLAIN_SCHEME = "postgresql://"

_UUID_COLUMNS = ("id", "event_id", "generation_id")


def _asyncpg_url(url: str) -> str:
    """`postgresql://` 만 `postgresql+asyncpg://` 로 바꾼다. 다른 드라이버 접미는 그대로 둔다."""
    if url.startswith(_PLAIN_SCHEME):
        return _ASYNCPG_SCHEME + url[len(_PLAIN_SCHEME) :]
    return url


def make_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """비동기 엔진을 만든다. 스킴 치환은 저장소에서 이 함수 한 곳에서만 한다."""
    return create_async_engine(_asyncpg_url(url), **kwargs)


def _to_uuid(value: str) -> uuid.UUID:
    """포트가 `str` 로 주는 식별자를 `uuid` 컬럼용으로 바꾼다."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def _row_to_job(row: Mapping[str, Any]) -> Job:
    """`RETURNING j.*` 한 줄을 `contracts.jobs.Job` 으로. uuid·jsonb 를 미러 타입에 맞춘다."""
    data: dict[str, Any] = dict(row)
    for column in _UUID_COLUMNS:
        value = data.get(column)
        if isinstance(value, uuid.UUID):
            data[column] = str(value)
    payload = data.get("payload")
    if isinstance(payload, str | bytes):
        data["payload"] = json.loads(payload)
    return Job.model_validate(data)


async def reap(engine: AsyncEngine) -> int:
    """`REAPER_SQL` 을 한 번 실행하고 회수한 행 수를 돌려준다."""
    async with AsyncSession(engine) as session:
        result = await session.execute(text(REAPER_SQL))
        await session.commit()
        return int(result.rowcount or 0)


class PostgresJobs:
    """`ports.jobs.JobsPort` 의 Postgres 구현. 호출마다 새 `AsyncSession`."""

    def __init__(self, engine: AsyncEngine, *, lease_s: float) -> None:
        self._engine = engine
        self._lease_s = float(lease_s)

    def _session(self) -> AsyncSession:
        return AsyncSession(self._engine)

    async def claim(self, kinds: Sequence[str], worker_id: str) -> Job | None:
        """02 §3.2 CTE. generation 은 claim 마다 새로 만든다. 커밋 뒤 반환한다."""
        statement = text(CLAIM_SQL).bindparams(bindparam("kinds", type_=ARRAY(Text)))
        params = {
            "kinds": list(kinds),
            "worker_id": worker_id,
            "generation_id": uuid.uuid4(),
            "lease_s": self._lease_s,
        }
        async with self._session() as session:
            result = await session.execute(statement, params)
            row = result.mappings().fetchone()
            await session.commit()
        return None if row is None else _row_to_job(row)

    async def _own(self, sql: str, params: dict[str, Any]) -> bool:
        """소유 조건 4개를 만족하는 한 행을 갱신한다. 0행이면 `False`."""
        async with self._session() as session:
            result = await session.execute(text(sql), params)
            await session.commit()
            return bool(result.rowcount)

    @staticmethod
    def _owner_params(job_id: str, worker_id: str, generation_id: str) -> dict[str, Any]:
        return {
            "id": _to_uuid(job_id),
            "worker_id": worker_id,
            "generation_id": _to_uuid(generation_id),
        }

    async def heartbeat(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        params = self._owner_params(job_id, worker_id, generation_id)
        params["lease_s"] = self._lease_s
        return await self._own(HEARTBEAT_SQL, params)

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return await self._own(COMPLETE_SQL, self._owner_params(job_id, worker_id, generation_id))

    async def fail(
        self,
        job_id: str,
        worker_id: str,
        generation_id: str,
        error_code: str,
        retry_after_s: float | None,
    ) -> bool:
        """`retry_after_s=None` 이면 `available_at = now()`(0초 뒤). attempts 는 건드리지 않는다."""
        params = self._owner_params(job_id, worker_id, generation_id)
        params["error_code"] = error_code
        params["retry_after_s"] = 0.0 if retry_after_s is None else float(retry_after_s)
        return await self._own(FAIL_SQL, params)

    async def release(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        return await self._own(RELEASE_SQL, self._owner_params(job_id, worker_id, generation_id))
