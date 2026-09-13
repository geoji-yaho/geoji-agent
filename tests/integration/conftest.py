"""통합 테스트 공용 fixture(02 §3.6).

로컬 docker 의 `postgres:16` 을 쓴다 — `docker compose -f docker-compose.dev.yml up -d postgres`.
접속은 **`TEST_DATABASE_URL`** 환경변수로만 받는다. 없으면 **실패**한다. 조용히 skip 하지 않는다
(testing 룰). `Settings` 필드가 아니므로 `.env.example` 에는 주석 줄로만 있다.

접속 문자열은 비밀값이다. 로그·단언 메시지에 원문을 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine
from geoji_ai.adapters.postgres_migrations import migrate
from geoji_ai.core.config import Settings
from geoji_ai.workers.dispatch import DEFAULT_ROUTE_BY_KIND, JOB_ROUTES, build_dedupe_key

TEST_DB_ENV = "TEST_DATABASE_URL"

#: 로컬 전용 role 3개. Supabase 의 role 은 백엔드가 만든다(10 §2). 비밀번호는 role 이름과 같다.
LOCAL_ROLES: tuple[str, ...] = ("ai_worker", "ai_api", "backend")

#: `Settings` 기본 lease. 환경을 읽지 않고 필드 기본값에서 가져온다.
DEFAULT_LEASE_SECONDS: int = int(Settings.model_fields["JOB_LEASE_SECONDS"].default)

_INSERT_JOB_SQL = text(
    """
    INSERT INTO ai.jobs (
        id, event_id, event_type, kind, dedupe_key,
        aggregate_id, aggregate_version, schema_version, payload,
        status, priority, attempts, max_attempts,
        available_at, deadline_at, trace_id
    ) VALUES (
        :id, :event_id, :event_type, :kind, :dedupe_key,
        :aggregate_id, :aggregate_version, 1, CAST(:payload AS jsonb),
        :status, :priority, :attempts, :max_attempts,
        COALESCE(CAST(:available_at AS timestamptz), now()),
        CAST(:deadline_at AS timestamptz), :trace_id
    )
    """
)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """`TEST_DATABASE_URL`. 없으면 실패한다(skip 이 아니다)."""
    url = os.environ.get(TEST_DB_ENV, "").strip()
    if not url:
        pytest.fail(
            f"{TEST_DB_ENV} 가 없다. 통합 테스트는 실제 Postgres 를 쓴다(02 §4.2). "
            "`docker compose -f docker-compose.dev.yml up -d postgres` 뒤 환경변수로 준다."
        )
    return url


async def _ensure_local_roles(url: str) -> None:
    engine = make_engine(url)
    try:
        async with engine.begin() as conn:
            for role in LOCAL_ROLES:
                found = (
                    await conn.execute(
                        text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": role}
                    )
                ).first()
                if found is None:
                    # role 이름은 위 상수뿐이라 값이 섞여 들어오지 않는다.
                    await conn.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{role}'"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _local_roles(test_database_url: str) -> None:
    """`ai_worker`·`ai_api`·`backend` 를 LOGIN role 로 만든다. 이미 있으면 그대로 둔다."""
    asyncio.run(_ensure_local_roles(test_database_url))


@pytest.fixture
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """테스트마다 `ai` 스키마를 지우고 러너로 001 을 다시 적용한다."""
    created = make_engine(test_database_url)
    async with created.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS ai CASCADE"))
    await migrate(created)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
def jobs(engine: AsyncEngine) -> PostgresJobs:
    return PostgresJobs(engine, lease_s=DEFAULT_LEASE_SECONDS)


@pytest.fixture
def make_jobs(engine: AsyncEngine) -> Callable[..., PostgresJobs]:
    """`make_jobs(lease_s=1)` — lease 만료 테스트용."""

    def _make(*, lease_s: float = DEFAULT_LEASE_SECONDS) -> PostgresJobs:
        return PostgresJobs(engine, lease_s=lease_s)

    return _make


@pytest.fixture
def enqueue(engine: AsyncEngine) -> Callable[..., Awaitable[str]]:
    """백엔드 자리에서 `ai.jobs` 에 직접 INSERT 한다. 워커는 INSERT 하지 않는다.

    기본값은 `workers.dispatch.JOB_ROUTES` 에서 온다. 명시로 넘긴 값이 이긴다.
    """
    sequence = itertools.count(1)

    async def _enqueue(
        kind: str,
        *,
        event_type: str | None = None,
        priority: int | None = None,
        max_attempts: int | None = None,
        deadline_at: datetime | None = None,
        available_at: datetime | None = None,
        attempts: int | None = None,
        status: str | None = None,
        dedupe_key: str | None = None,
        **payload: Any,
    ) -> str:
        route = JOB_ROUTES[event_type] if event_type is not None else DEFAULT_ROUTE_BY_KIND[kind]
        order = next(sequence)
        if dedupe_key is None:
            # 같은 payload 를 여러 번 넣는 테스트가 있어 호출 순번으로 유일하게 만든다.
            dedupe_key = f"{build_dedupe_key(route, payload)}#{order}"
        if deadline_at is None and route.deadline_after_s is not None:
            deadline_at = datetime.now(UTC) + timedelta(seconds=route.deadline_after_s)
        id_field, version_field = route.aggregate_fields
        job_id = str(uuid4())
        params = {
            "id": job_id,
            "event_id": str(uuid4()),
            "event_type": route.event_type,
            "kind": route.kind,
            "dedupe_key": dedupe_key,
            "aggregate_id": str(payload[id_field]),
            "aggregate_version": int(payload[version_field]),
            "payload": json.dumps(payload, ensure_ascii=False),
            "status": "QUEUED" if status is None else status,
            "priority": route.priority if priority is None else priority,
            "attempts": 0 if attempts is None else attempts,
            "max_attempts": route.max_attempts if max_attempts is None else max_attempts,
            "available_at": available_at,
            "deadline_at": deadline_at,
            "trace_id": str(uuid4()),
        }
        async with engine.begin() as conn:
            await conn.execute(_INSERT_JOB_SQL, params)
        return job_id

    return _enqueue


@pytest.fixture
def fetch_job(engine: AsyncEngine) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """job id → `ai.jobs` 행 하나를 dict 로."""

    async def _fetch(job_id: str) -> dict[str, Any]:
        async with engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT * FROM ai.jobs WHERE id = CAST(:id AS uuid)"), {"id": job_id}
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    return _fetch


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """이 디렉터리의 테스트 전부에 `integration` 마커를 붙인다.

    `pyproject.toml` 에 마커를 등록해 두고 파일 하나에만 달면 `-m integration` 이
    통합 테스트의 일부만 고른다. 붙이는 곳을 한 곳으로 모은다.
    """
    for item in items:
        item.add_marker(pytest.mark.integration)
