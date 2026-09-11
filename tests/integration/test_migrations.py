"""마이그레이션 러너와 001 DDL(02 §3.1·§3.6·§4.2).

실제 Postgres 를 쓴다. `TEST_DATABASE_URL` 이 없으면 conftest 가 실패시킨다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.adapters.postgres_migrations import MAX_OWNED_VERSION, migrate

#: 권한 부족(`InsufficientPrivilege`)의 SQLSTATE.
INSUFFICIENT_PRIVILEGE = "42501"


async def _scalar(engine: AsyncEngine, sql: str) -> object:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar()


async def _versions(engine: AsyncEngine) -> list[int]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT version FROM ai.schema_migrations ORDER BY version")
        )
        return [int(value) for value in result.scalars().all()]


async def _index_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'ai' AND tablename = 'jobs'")
        )
        return set(result.scalars().all())


def _role_url(url: str, role: str) -> str:
    """같은 서버에 다른 role 로 접속하는 URL. 비밀번호는 role 이름과 같다(로컬 전용)."""
    return make_url(url).set(username=role, password=role).render_as_string(hide_password=False)


async def test_001_을_적용하면_테이블과_인덱스와_적용기록이_생긴다(engine: AsyncEngine) -> None:
    # `engine` fixture 가 `ai` 스키마를 지우고 러너로 001 을 적용한 직후 상태다.
    assert await _scalar(engine, "SELECT to_regclass('ai.jobs')") == "ai.jobs"
    assert {"jobs_claim_idx", "jobs_lease_idx"} <= await _index_names(engine)
    assert await _versions(engine) == [1]


async def test_재적용은_no_op_이다(engine: AsyncEngine) -> None:
    applied = await migrate(engine)

    assert applied == []
    assert await _versions(engine) == [1]


async def test_ai_worker_는_insert_가_막히고_select_update_는_된다(
    engine: AsyncEngine,
    test_database_url: str,
    enqueue: Callable[..., Awaitable[str]],
) -> None:
    await enqueue("PREPARE", post_id="p1", post_version=1, audience_version=1)
    worker_engine = make_engine(_role_url(test_database_url, "ai_worker"))
    try:
        with pytest.raises(DBAPIError) as caught:
            async with worker_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO ai.jobs (id, event_id, event_type, kind, dedupe_key,"
                        " aggregate_id, aggregate_version, payload, priority, max_attempts,"
                        " trace_id)"
                        " VALUES (gen_random_uuid(), gen_random_uuid(), 'post.created', 'PREPARE',"
                        " 'prepare:x:1:1', 'x', 1, '{}'::jsonb, 30, 2, 't')"
                    )
                )
        assert getattr(caught.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE

        async with worker_engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM ai.jobs"))).scalar() == 1
        async with worker_engine.begin() as conn:
            result = await conn.execute(text("UPDATE ai.jobs SET updated_at = now()"))
            assert result.rowcount == 1
    finally:
        await worker_engine.dispose()


async def test_러너는_004_를_읽지도_적용하지도_않는다(engine: AsyncEngine, tmp_path: Path) -> None:
    assert MAX_OWNED_VERSION == 3  # 004 부터는 백엔드 소유다(02 §3.6)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS ai CASCADE"))
    (tmp_path / "001_fake.sql").write_text(
        "CREATE SCHEMA IF NOT EXISTS ai;\nCREATE TABLE ai.fake_one (id integer PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (tmp_path / "004_backend.sql").write_text(
        "CREATE TABLE ai.fake_four (id integer PRIMARY KEY);\n", encoding="utf-8"
    )

    applied = await migrate(engine, migrations_dir=tmp_path)

    assert applied == [1]
    assert await _versions(engine) == [1]
    assert await _scalar(engine, "SELECT to_regclass('ai.fake_one')") == "ai.fake_one"
    assert await _scalar(engine, "SELECT to_regclass('ai.fake_four')") is None
