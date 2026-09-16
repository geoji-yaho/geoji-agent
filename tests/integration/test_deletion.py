"""삭제·무효화·늦은 retain(04 §3.5·§4.2 `test_deletion`, ME-06 일부).

⑩ 무효화 SQL: evidence·dossiers·trial_prep·memory_facts·node_results 표시(다른 scope 불변)
⑪ 늦은 RETAIN 3조건 각각 skip — "먼저 실패시킬 케이스"(삭제 후 늦은 retain 부활)
⑫ `delete_user/room/post` 소프트 삭제 후 recall 0
⑬ 무효화 SQL 을 `backend` role 로 실행해 권한 오류 없음
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.adapters.postgres_memory import PostgresMemory, invalidate_scope
from geoji_ai.application.retain_memory import build_verdict_payload
from tests.integration.test_postgres_memory import insert_fact, seed_dossier
from tests.integration.test_retain_handler import (
    AUTHOR_ID,
    MATCHING_EPOCHS,
    ROOM_ID,
    SnapshotBackend,
    fact_rows,
    make_snapshot,
    processed_count,
    run_retain,
    seed_epochs,
)
from tests.integration.test_worker_runtime import make_settings

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]


# --- ⑪ 늦은 RETAIN 3조건 -----------------------------------------------------------


async def test_늦은_RETAIN_1_이미_처리한_event_면_skip(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    event_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO ai.processed_memory_events (event_id) VALUES (CAST(:e AS uuid))"),
            {"e": event_id},
        )
    backend = SnapshotBackend(make_snapshot())

    job_id = await run_retain(
        engine=engine, jobs=jobs, enqueue=enqueue, backend=backend, event_id=event_id
    )

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []
    assert await processed_count(engine) == 1


async def test_늦은_RETAIN_2_snapshot_404_면_skip(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    backend = SnapshotBackend(not_found=True)

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    row = await fetch_job(job_id)
    assert row["status"] == "SUCCEEDED"
    assert row["last_error_code"] is None
    assert backend.snapshot_calls == 1
    assert await fact_rows(engine) == []


async def test_늦은_RETAIN_3_삭제로_epoch_가_올랐으면_skip(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    # 스냅샷은 epoch 1 로 만들어졌고, 그 뒤 작성자 탈퇴로 백엔드가 user epoch 를 2 로 올렸다.
    await seed_epochs(engine, {f"user:{AUTHOR_ID}": 2, f"room:{ROOM_ID}": 1})
    backend = SnapshotBackend(make_snapshot())

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []
    assert await processed_count(engine) == 0


class _EpochBumpedAfterCommit(PostgresMemory):
    """retain 트랜잭션 커밋 직후, 재확인 전에 백엔드가 epoch 를 올린 창을 만든다."""

    async def _revoke_if_epochs_moved(self, ids, privacy_versions):  # type: ignore[override]
        await seed_epochs(self._engine, {f"user:{AUTHOR_ID}": 2})
        return await super()._revoke_if_epochs_moved(ids, privacy_versions)


async def test_늦은_RETAIN_3_커밋_직후_epoch_가_오르면_방금_넣은_행을_지운다(
    engine: AsyncEngine,
    privacy_epochs: str,
):
    # 검증 M1: 트랜잭션 안 epoch 검사는 통과했지만 커밋 전에 삭제가 커밋된 경우.
    await seed_epochs(engine, MATCHING_EPOCHS)
    payload = build_verdict_payload(make_snapshot())
    assert payload is not None
    memory = _EpochBumpedAfterCommit(engine, make_settings())

    written = await memory.retain_verdict(str(uuid4()), payload)

    assert written == 0
    rows = await fact_rows(engine)
    assert len(rows) == 3
    assert all(row["deleted_at"] is not None for row in rows)
    assert await processed_count(engine) == 1


async def test_늦은_RETAIN_3_커밋_뒤_epoch_그대로면_행을_둔다(
    engine: AsyncEngine,
    privacy_epochs: str,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    payload = build_verdict_payload(make_snapshot())
    assert payload is not None

    written = await PostgresMemory(engine, make_settings()).retain_verdict(str(uuid4()), payload)

    assert written == 3
    assert all(row["deleted_at"] is None for row in await fact_rows(engine))


async def test_늦은_RETAIN_3_epoch_행이_없으면_0_으로_본다(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    # 테이블에 행이 없으면 epoch 0. 스냅샷은 1 이라 불일치 → skip.
    backend = SnapshotBackend(make_snapshot())

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []


# --- ⑩ ⑬ 무효화 SQL ---------------------------------------------------------------

HIT_POST = "post-deleted"
OTHER_POST = "post-kept"


def _evidence(label: str, fact_type: str, source_type: str, source_id: str) -> dict[str, Any]:
    return {
        "label": label,
        "fact_type": fact_type,
        "text": f"{label} 근거",
        "scope": {"visibility": "ROOMS", "room_ids": ["r1"]},
        "source_type": source_type,
        "source_id": source_id,
        "source_version": 1,
    }


async def _seed_invalidation_world(conn: AsyncConnection) -> None:
    """삭제 대상 post 와 무관한 post 를 나란히 둔다."""
    hit_dossier = await seed_dossier(
        conn,
        post_id=HIT_POST,
        evidences=[
            _evidence("F0", "SPEND", "POST", HIT_POST),
            _evidence("F1", "RULE", "RULE", "rule-1"),
        ],
    )
    other_dossier = await seed_dossier(
        conn, post_id=OTHER_POST, evidences=[_evidence("F0", "SPEND", "POST", OTHER_POST)]
    )
    for post_id, dossier_id in ((HIT_POST, hit_dossier), (OTHER_POST, other_dossier)):
        await conn.execute(
            text(
                "INSERT INTO ai.trial_prep (id, post_id, post_version, audience_version, "
                "privacy_versions, prompt_version, input_hash, status, dossier_id) VALUES "
                "(:id, :p, 1, 1, '[]'::jsonb, 'v1', 'h', 'COMPLETE', CAST(:d AS uuid))"
            ),
            {"id": uuid4(), "p": post_id, "d": dossier_id},
        )
        await conn.execute(
            text(
                "INSERT INTO ai.memory_facts (id, bank_type, bank_id, fact_type, epistemic_type, "
                "source_type, source_id, source_version, payload, scope, occurred_at) VALUES "
                "(:id, 'user', 'u1', 'SPEND', 'DB_RECORD', 'POST', :p, 1, "
                "CAST(:payload AS jsonb), '{}'::jsonb, now())"
            ),
            {"id": uuid4(), "p": post_id, "payload": json.dumps({"post_id": post_id})},
        )
        call_id = uuid4()
        await conn.execute(
            text(
                "INSERT INTO ai.llm_calls (id, post_id, node, call_index, vendor, model_id, "
                "request_hash, status, estimated_max_micro_usd) VALUES "
                "(:id, :p, 'writer', 0, 'xai', 'm', 'rh', 'COMPLETE', 1)"
            ),
            {"id": call_id, "p": post_id},
        )
        privacy_versions = [
            {"scope_key": f"post:{post_id}", "epoch": 1},
            {"scope_key": "room:r1", "epoch": 1},
        ]
        await conn.execute(
            text(
                "INSERT INTO ai.node_results (call_id, request_hash, model_id, prompt_version, "
                "policy_version, privacy_versions, validated_output, expires_at) VALUES "
                "(:id, 'rh', 'm', 'v1', 'guardrail-v2', CAST(:pv AS jsonb), '{}'::jsonb, "
                "now() + interval '1 day')"
            ),
            {"id": call_id, "pv": json.dumps(privacy_versions)},
        )


async def _invalidation_state(engine: AsyncEngine) -> dict[str, Any]:
    queries = {
        "evidence": "SELECT d.post_id || '/' || e.label, e.invalidated_at IS NOT NULL "
        "FROM ai.evidence e JOIN ai.dossiers d ON d.id = e.dossier_id",
        "dossiers": "SELECT post_id, invalidated_at IS NOT NULL FROM ai.dossiers",
        "prep": "SELECT post_id, status || ':' || (invalidated_at IS NOT NULL)::text "
        "FROM ai.trial_prep",
        "facts": "SELECT source_id, deleted_at IS NOT NULL FROM ai.memory_facts",
        "nodes": "SELECT c.post_id, n.invalidated_at IS NOT NULL FROM ai.node_results n "
        "JOIN ai.llm_calls c ON c.id = n.call_id",
    }
    state: dict[str, Any] = {}
    async with engine.connect() as conn:
        for name, sql in queries.items():
            state[name] = {row[0]: row[1] for row in await conn.execute(text(sql))}
    return state


_EXPECTED_AFTER_INVALIDATION: dict[str, Any] = {
    "evidence": {f"{HIT_POST}/F0": True, f"{HIT_POST}/F1": False, f"{OTHER_POST}/F0": False},
    "dossiers": {HIT_POST: True, OTHER_POST: False},
    "prep": {HIT_POST: "INVALIDATED:true", OTHER_POST: "COMPLETE:false"},
    "facts": {HIT_POST: True, OTHER_POST: False},
    "nodes": {HIT_POST: True, OTHER_POST: False},
}


async def test_무효화_SQL_이_파생물을_표시하고_다른_scope_는_둔다(engine: AsyncEngine):  # ⑩
    async with engine.begin() as conn:
        await _seed_invalidation_world(conn)

    async with engine.begin() as conn:
        await invalidate_scope(
            conn, source_type="POST", source_id=HIT_POST, scope_key=f"post:{HIT_POST}"
        )

    assert await _invalidation_state(engine) == _EXPECTED_AFTER_INVALIDATION


async def test_무효화_SQL_node_results_는_객체_배열의_scope_key_로_매치(  # ⑩
    engine: AsyncEngine,
):
    async with engine.begin() as conn:
        await _seed_invalidation_world(conn)
        # 원문 `?` 로는 [{scope_key, epoch}] 에서 매치되지 않는다(조건을 `@>` 로 바꾼 이유).
        question = await conn.execute(
            text("SELECT count(*) FROM ai.node_results WHERE jsonb_exists(privacy_versions, :k)"),
            {"k": "room:r1"},
        )
        assert question.scalar_one() == 0

    async with engine.begin() as conn:
        await invalidate_scope(conn, source_type="ROOM", source_id="r1", scope_key="room:r1")

    state = await _invalidation_state(engine)
    assert state["nodes"] == {HIT_POST: True, OTHER_POST: True}
    assert state["dossiers"] == {HIT_POST: False, OTHER_POST: False}


async def test_무효화_SQL_을_backend_role_로_실행(engine: AsyncEngine):  # ⑬
    async with engine.begin() as conn:
        await _seed_invalidation_world(conn)

    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE backend"))
        assert (await conn.execute(text("SELECT current_user"))).scalar_one() == "backend"
        await invalidate_scope(
            conn, source_type="POST", source_id=HIT_POST, scope_key=f"post:{HIT_POST}"
        )

    assert await _invalidation_state(engine) == _EXPECTED_AFTER_INVALIDATION


# --- ⑫ delete_* 소프트 삭제 ---------------------------------------------------------

_CATEGORY = "교통/택시"


def _before() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


async def _seed_bank(engine: AsyncEngine, user_id: str, room_id: str, post_id: str) -> None:
    """사용자 뱅크 2행 + 방 뱅크 VERDICT 3·RULE_HIT 1·COMMENT 1."""
    recent = datetime.now(UTC) - timedelta(days=1)
    await insert_fact(engine, bank_id=user_id, payload={"post_id": post_id, "category": _CATEGORY})
    await insert_fact(
        engine,
        bank_id=user_id,
        fact_type="VERDICT",
        source_type="VERDICT",
        payload={"post_id": post_id, "result": "guilty"},
    )
    for index in range(3):
        await insert_fact(
            engine,
            bank_type="room",
            bank_id=room_id,
            fact_type="VERDICT",
            source_type="VERDICT",
            payload={
                "post_id": f"{post_id}-{index}" if index else post_id,
                "author_id": user_id,
                "category": _CATEGORY,
                "result": "guilty",
            },
        )
    await insert_fact(
        engine,
        bank_type="room",
        bank_id=room_id,
        fact_type="RULE_HIT",
        source_type="RULE",
        payload={"post_id": post_id, "category": _CATEGORY},
        occurred_at=recent,
    )
    await insert_fact(
        engine,
        bank_type="room",
        bank_id=room_id,
        fact_type="COMMENT",
        source_type="COMMENT",
        payload={"post_id": post_id, "author_id": "friend"},
        occurred_at=recent,
    )


def _flag_on_memory(engine: AsyncEngine) -> PostgresMemory:
    return PostgresMemory(engine, make_settings(ROOM_COMMENT_STYLE_ENABLED=True))


async def test_delete_user_뒤_recall_0(engine: AsyncEngine):  # ⑫
    await _seed_bank(engine, "u1", "r1", "p1")
    await _seed_bank(engine, "u2", "r2", "p2")
    memory = _flag_on_memory(engine)
    assert len(await memory.recall_user("u1", _CATEGORY, _before(), 20)) == 2

    deleted = await memory.delete_user("u1")

    # 사용자 뱅크 2 + 방 뱅크에서 u1 이 작성자인 VERDICT 3
    assert deleted == 5
    assert await memory.recall_user("u1", _CATEGORY, _before(), 20) == []
    assert (await memory.recall_room("r1", _CATEGORY)).strictness is None
    assert len(await memory.recall_user("u2", _CATEGORY, _before(), 20)) == 2
    assert await memory.delete_user("u1") == 0


async def test_delete_room_뒤_recall_room_0(engine: AsyncEngine):  # ⑫
    await _seed_bank(engine, "u1", "r1", "p1")
    memory = _flag_on_memory(engine)
    before = await memory.recall_room("r1", _CATEGORY)
    assert before.rules_hit and before.style_example_refs and before.strictness is not None

    deleted = await memory.delete_room("r1")

    assert deleted == 5
    after = await memory.recall_room("r1", _CATEGORY)
    assert (after.rules_hit, after.style_example_refs, after.strictness) == ([], [], None)
    # 사용자 뱅크는 방 삭제와 무관하다.
    assert len(await memory.recall_user("u1", _CATEGORY, _before(), 20)) == 2


async def test_delete_post_뒤_그_post_기억_0(engine: AsyncEngine):  # ⑫
    await _seed_bank(engine, "u1", "r1", "p1")
    memory = _flag_on_memory(engine)

    deleted = await memory.delete_post("p1")

    # 사용자 SPEND·VERDICT + 방 VERDICT(p1) + RULE_HIT + COMMENT
    assert deleted == 5
    assert await memory.recall_user("u1", _CATEGORY, _before(), 20) == []
    after = await memory.recall_room("r1", _CATEGORY)
    assert (after.rules_hit, after.style_example_refs, after.strictness) == ([], [], None)


async def test_backend_POST_무효화_SQL도_파생_기억을_전부_지운다(engine: AsyncEngine):
    """Q2: source_id가 verdict/comment/rule ID여도 payload.post_id로 삭제 범위에 포함한다."""
    await _seed_bank(engine, "u1", "r1", "p1")
    await _seed_bank(engine, "u2", "r2", "p2")
    memory = _flag_on_memory(engine)

    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE backend"))
        await invalidate_scope(conn, source_type="POST", source_id="p1", scope_key="post:p1")

    assert await memory.recall_user("u1", _CATEGORY, _before(), 20) == []
    after = await memory.recall_room("r1", _CATEGORY)
    assert (after.rules_hit, after.style_example_refs, after.strictness) == ([], [], None)
    assert len(await memory.recall_user("u2", _CATEGORY, _before(), 20)) == 2
    async with engine.connect() as conn:
        deleted = await conn.execute(
            text("SELECT count(*) FROM ai.memory_facts WHERE deleted_at IS NOT NULL")
        )
        assert deleted.scalar_one() == 5
