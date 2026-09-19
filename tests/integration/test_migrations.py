"""마이그레이션 러너와 001 DDL(02 §3.1·§3.6·§4.2).

실제 Postgres 를 쓴다. `TEST_DATABASE_URL` 이 없으면 conftest 가 실패시킨다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.adapters.postgres_migrations import (
    BACKEND_OWNED_VERSIONS,
    MAX_OWNED_VERSION,
    migrate,
)

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
    # 002·003·006 이 생겨 fixture 가 넷을 모두 적용한다(004 는 백엔드, 005 는 파일 없음).
    assert await _versions(engine) == [1, 2, 3, 6]


async def test_재적용은_no_op_이다(engine: AsyncEngine) -> None:
    applied = await migrate(engine)

    assert applied == []
    assert await _versions(engine) == [1, 2, 3, 6]


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


async def test_빈_DB_에_migrate_를_동시에_두_번_돌려도_실패하지_않는다(
    engine: AsyncEngine, test_database_url: str
) -> None:
    # 부트스트랩(`CREATE SCHEMA`·`CREATE TABLE IF NOT EXISTS`)도 잠금 아래여야 한다.
    # 경합은 비결정적이라 여러 번 돌린다. 엔진을 따로 두고 미리 연결해 출발을 맞춘다.
    for _ in range(5):
        first = make_engine(test_database_url)
        second = make_engine(test_database_url)
        try:
            for warm in (first, second):
                async with warm.connect():
                    pass
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA IF EXISTS ai CASCADE"))

            results = await asyncio.gather(migrate(first), migrate(second))
        finally:
            await first.dispose()
            await second.dispose()

        assert sorted(results) == [[], [1, 2, 3, 6]]
        assert await _versions(engine) == [1, 2, 3, 6]


async def test_러너는_004_를_읽지도_적용하지도_않는다(engine: AsyncEngine, tmp_path: Path) -> None:
    # 18 §3.1: 상한은 006, 004 만 백엔드 소유로 건너뛴다(005 는 P1 예약이라 파일이 없다).
    assert MAX_OWNED_VERSION == 6
    assert BACKEND_OWNED_VERSIONS == frozenset({4})
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS ai CASCADE"))
    (tmp_path / "001_fake.sql").write_text(
        "CREATE SCHEMA IF NOT EXISTS ai;\nCREATE TABLE ai.fake_one (id integer PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (tmp_path / "004_backend.sql").write_text(
        "CREATE TABLE ai.fake_four (id integer PRIMARY KEY);\n", encoding="utf-8"
    )
    (tmp_path / "006_ours.sql").write_text(
        "CREATE TABLE ai.fake_six (id integer PRIMARY KEY);\n", encoding="utf-8"
    )

    applied = await migrate(engine, migrations_dir=tmp_path)

    assert applied == [1, 6]
    assert await _versions(engine) == [1, 6]
    assert await _scalar(engine, "SELECT to_regclass('ai.fake_one')") == "ai.fake_one"
    assert await _scalar(engine, "SELECT to_regclass('ai.fake_six')") == "ai.fake_six"
    assert await _scalar(engine, "SELECT to_regclass('ai.fake_four')") is None


async def test_006_을_적용하면_JURY_VOTE_INSERT_가_통과한다(engine: AsyncEngine) -> None:
    """18 §3.1 = 00 §8.1. 001 의 열 CHECK 를 006 이 갈아 끼운다."""
    assert 6 in await _versions(engine)
    inserted = await _execute(
        engine,
        "INSERT INTO ai.jobs (id, event_id, event_type, kind, dedupe_key,"
        " aggregate_id, aggregate_version, payload, priority, max_attempts, trace_id)"
        " VALUES (gen_random_uuid(), gen_random_uuid(), 'jury.vote_requested', 'JURY_VOTE',"
        " 'jury-vote:p1:r1:bot1', 'p1', 1,"
        # `:1` 은 text() 가 바인드로 읽는다. 콜론 뒤에 공백을 둔다
        """ '{"post_id": "p1", "post_version": 1, "room_id": "r1", "voter_id": "bot1"}'::jsonb,"""
        " 60, 2, 't')",
    )
    assert inserted == 1
    assert await _scalar(engine, "SELECT count(*) FROM ai.jobs WHERE kind = 'JURY_VOTE'") == 1


# --- 002·003 (04 §3.1·§4.1, grants 는 10 §1) ---------------------------------------------

UNIQUE_VIOLATION = "23505"

TABLES_002_003 = (
    "trial_prep",
    "dossiers",
    "evidence",
    "evidence_sources",
    "banter_examples",
    "memory_facts",
    "processed_memory_events",
    "case_budgets",
    "llm_calls",
    "node_results",
)

_INSERT_MEMORY_FACT = (
    "INSERT INTO ai.memory_facts (id, bank_type, bank_id, fact_type, epistemic_type,"
    " source_type, source_id, source_version, payload, scope, occurred_at)"
    " VALUES (gen_random_uuid(), 'user', 'u1', 'VERDICT', 'DB_RECORD',"
    " 'verdict', 'v1', 1, '{}'::jsonb, '{}'::jsonb, now())"
)

_INSERT_LLM_CALL = (
    "INSERT INTO ai.llm_calls (id, generation_id, node, call_index, vendor, model_id,"
    " request_hash, status, estimated_max_micro_usd)"
    " VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', 'writer', 0,"
    " 'fake', 'fake-model', 'h', 'RESERVED', 100)"
)

_INSERT_TRIAL_PREP = (
    "INSERT INTO ai.trial_prep (id, post_id, post_version, audience_version, privacy_versions,"
    " prompt_version, input_hash, status)"
    " VALUES (gen_random_uuid(), 'p1', 1, 1, '[]'::jsonb, 'pv1', 'ih1', 'DOSSIER_READY')"
)


async def _execute(engine: AsyncEngine, sql: str) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(text(sql))).rowcount


async def _sqlstate_of(engine: AsyncEngine, sql: str) -> str | None:
    with pytest.raises(DBAPIError) as caught:
        await _execute(engine, sql)
    return getattr(caught.value.orig, "sqlstate", None)


async def test_001_003_을_적용하면_10_테이블이_생기고_재적용은_no_op_이다(
    engine: AsyncEngine,
) -> None:
    for table in TABLES_002_003:
        assert await _scalar(engine, f"SELECT to_regclass('ai.{table}')") == f"ai.{table}"

    assert await migrate(engine) == []
    assert await _scalar(engine, "SELECT count(*) FROM ai.schema_migrations") == 4


async def test_ai_worker_는_memory_facts_에_insert_할_수_있다(
    engine: AsyncEngine, test_database_url: str
) -> None:
    worker_engine = make_engine(_role_url(test_database_url, "ai_worker"))
    try:
        assert await _execute(worker_engine, _INSERT_MEMORY_FACT) == 1
    finally:
        await worker_engine.dispose()
    assert await _scalar(engine, "SELECT count(*) FROM ai.memory_facts") == 1


async def test_backend_는_evidence_insert_가_막히고_update_는_된다(
    engine: AsyncEngine, test_database_url: str
) -> None:
    await _execute(
        engine,
        "INSERT INTO ai.dossiers (id, post_id, snapshot_hash, label_map, privacy_versions)"
        " VALUES ('00000000-0000-0000-0000-0000000000d1', 'p1', 'sh', '{}'::jsonb, '[]'::jsonb)",
    )
    await _execute(
        engine,
        "INSERT INTO ai.evidence (id, dossier_id, label, epistemic_type, fact_type, text, scope)"
        " VALUES (gen_random_uuid(), '00000000-0000-0000-0000-0000000000d1', 'F0',"
        " 'DB_RECORD', 'SPEND', '택시 12000원', '{}'::jsonb)",
    )
    backend_engine = make_engine(_role_url(test_database_url, "backend"))
    try:
        state = await _sqlstate_of(
            backend_engine,
            "INSERT INTO ai.evidence (id, dossier_id, label, epistemic_type, fact_type, text,"
            " scope) VALUES (gen_random_uuid(), '00000000-0000-0000-0000-0000000000d1', 'F1',"
            " 'DB_RECORD', 'SPEND', 'x', '{}'::jsonb)",
        )
        assert state == INSUFFICIENT_PRIVILEGE

        updated = await _execute(
            backend_engine,
            "UPDATE ai.evidence SET invalidated_at = now()"
            " WHERE dossier_id = '00000000-0000-0000-0000-0000000000d1'",
        )
        assert updated == 1
    finally:
        await backend_engine.dispose()


async def test_ai_api_는_llm_calls_insert_는_되고_memory_facts_select_는_막힌다(
    engine: AsyncEngine, test_database_url: str
) -> None:
    api_engine = make_engine(_role_url(test_database_url, "ai_api"))
    try:
        assert await _execute(api_engine, _INSERT_LLM_CALL) == 1
        state = await _sqlstate_of(api_engine, "SELECT count(*) FROM ai.memory_facts")
        assert state == INSUFFICIENT_PRIVILEGE
    finally:
        await api_engine.dispose()


@pytest.mark.parametrize(
    "insert_sql",
    [_INSERT_TRIAL_PREP, _INSERT_MEMORY_FACT, _INSERT_LLM_CALL],
    ids=["trial_prep", "memory_facts", "llm_calls"],
)
async def test_002_003_의_UNIQUE_는_중복_insert_를_막는다(
    engine: AsyncEngine, insert_sql: str
) -> None:
    # id 는 매번 새 uuid 라 PK 가 아니라 UNIQUE 제약이 막는다.
    assert await _execute(engine, insert_sql) == 1
    assert await _sqlstate_of(engine, insert_sql) == UNIQUE_VIOLATION


async def test_privacy_epochs_fixture_는_ai_worker_에_select_만_준다(
    engine: AsyncEngine, test_database_url: str, privacy_epochs: str
) -> None:
    await _execute(engine, f"INSERT INTO {privacy_epochs} (scope_key, epoch) VALUES ('user:u1', 1)")
    worker_engine = make_engine(_role_url(test_database_url, "ai_worker"))
    try:
        assert await _scalar(worker_engine, f"SELECT epoch FROM {privacy_epochs}") == 1
        state = await _sqlstate_of(
            worker_engine, f"INSERT INTO {privacy_epochs} (scope_key) VALUES ('room:r1')"
        )
        assert state == INSUFFICIENT_PRIVILEGE
    finally:
        await worker_engine.dispose()
