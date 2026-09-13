"""RETAIN 핸들러 흐름(04 §3.3·§3.5, ME-03).

실제 Postgres + `PostgresMemory` + 테스트 안의 작은 가짜 `BackendPort`(snapshot 만).

⑭ sentence.finalized 정상 → 행 생성·complete
⑮ verdict_final None → 행 0·complete
⑯ comment.approved 안전 필터 탈락 → 행 0
⑰ epoch 불일치 → 행 0

이 파일의 헬퍼(`make_snapshot`·`SnapshotBackend`·`run_retain` 등)는 `test_deletion` 도 쓴다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendRejected, BackendUnavailable
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.adapters.postgres_memory import PostgresMemory
from geoji_ai.application.retain_memory import RetainHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.workers.dispatch import HANDLERS, HandlerContext
from tests.integration.test_worker_runtime import make_settings

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

_FIXTURE = (
    Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "case-snapshot-taxi.json"
)

AUTHOR_ID = "user-01H8Z9QK"
ROOM_ID = "room-ddegeoji-01"
POST_ID = "post-taxi-20260907-0852"
VERDICT_ID = "7a1d9c40-3b52-4e18-9f0a-2c6d8b4e1f31"
COMMENT_ID = "comment-1"
WORKER_ID = "test-host:1:BACKGROUND"

VERDICT_FINAL: dict[str, Any] = {
    "sentence": "oneDay",
    "sentence_source": "AI",
    "sentencing_reason": "늦잠 핑계로 택시를 탔다",
    "reason_source": "AI",
    "applied_intensity": "spicy",
    "banter_strategy": None,
}

SAFE_COMMENT_CONTENT = "지하철 첫차 시간을 알람으로 맞춰 두면 택시 탈 일이 줄어듭니다"


def make_snapshot(**over: Any) -> CaseSnapshot:
    """taxi 스냅샷 + RETAIN 확장. `over` 의 키가 최상위 필드를 덮는다(None 으로 지우기 포함)."""
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    data["verdict_final"] = dict(VERDICT_FINAL)
    data["comment"] = {
        "comment_id": COMMENT_ID,
        "version": 1,
        "room_id": ROOM_ID,
        "post_id": POST_ID,
        "post_status": "JUDGED",
        "author_id": "user-friend-02",
        "content": SAFE_COMMENT_CONTENT,
        "created_at": "2026-09-07T10:00:00Z",
    }
    data.update(over)
    return CaseSnapshot.model_validate(data)


class SnapshotBackend:
    """`snapshot` 만 쓰는 가짜 `BackendPort`. 다른 메서드는 부르면 실패한다."""

    def __init__(
        self,
        snapshot: CaseSnapshot | None = None,
        *,
        not_found: bool = False,
        unavailable: bool = False,
    ) -> None:
        self._snapshot = snapshot
        self._not_found = not_found
        self._unavailable = unavailable
        self.snapshot_calls = 0

    async def snapshot(self, job_id: str, generation_id: str) -> CaseSnapshot:
        self.snapshot_calls += 1
        if self._not_found:
            raise BackendRejected(404, "NOT_FOUND")
        if self._unavailable:
            raise BackendUnavailable()
        assert self._snapshot is not None
        return self._snapshot

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"RETAIN 핸들러가 부르면 안 되는 백엔드 메서드: {name}")


async def seed_epochs(engine: AsyncEngine, epochs: dict[str, int]) -> None:
    """`privacy_epochs` fixture 가 만든 테이블에 현재 epoch 를 넣는다(백엔드 자리)."""
    async with engine.begin() as conn:
        for scope_key, epoch in epochs.items():
            await conn.execute(
                text(
                    "INSERT INTO ai.privacy_epochs (scope_key, epoch) VALUES (:k, :e) "
                    "ON CONFLICT (scope_key) DO UPDATE SET epoch = EXCLUDED.epoch"
                ),
                {"k": scope_key, "e": epoch},
            )


#: taxi 스냅샷의 `privacy_versions` 와 같은 값.
MATCHING_EPOCHS: dict[str, int] = {f"user:{AUTHOR_ID}": 1, f"room:{ROOM_ID}": 1}


async def run_retain(
    *,
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    backend: SnapshotBackend,
    event_type: str = "sentence.finalized",
    event_id: str | None = None,
) -> str:
    """RETAIN job 을 넣고 claim 한 뒤 `HANDLERS["RETAIN"]` 을 한 번 부른다. job id 를 돌려준다."""
    if event_type == "sentence.finalized":
        payload = {"event": event_type, "verdict_id": VERDICT_ID, "comment_id": None, "version": 1}
    else:
        payload = {"event": event_type, "verdict_id": None, "comment_id": COMMENT_ID, "version": 1}
    job_id = await enqueue("RETAIN", event_type=event_type, **payload)
    if event_id is not None:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE ai.jobs SET event_id = CAST(:e AS uuid) WHERE id = CAST(:id AS uuid)"),
                {"e": event_id, "id": job_id},
            )
    job = await jobs.claim(("RETAIN",), WORKER_ID)
    assert job is not None and job.id == job_id
    settings = make_settings()
    ctx = HandlerContext(
        jobs=jobs,
        semaphore=asyncio.Semaphore(1),
        settings=settings,
        generation_id=job.generation_id or "",
        worker_id=WORKER_ID,
        backend=backend,  # type: ignore[arg-type]
        memory=PostgresMemory(engine, settings),
    )
    await HANDLERS["RETAIN"](job, ctx)
    return job_id


async def fact_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT bank_type, bank_id, fact_type, epistemic_type, source_type, source_id, "
                "source_version, payload, scope, occurred_at, deleted_at FROM ai.memory_facts "
                "ORDER BY bank_type, bank_id, fact_type, source_id"
            )
        )
        return [dict(row) for row in result.mappings()]


async def processed_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM ai.processed_memory_events"))
        return int(result.scalar_one())


# --- 테스트 ---------------------------------------------------------------------


def test_RETAIN_핸들러가_교체됐다():
    assert isinstance(HANDLERS["RETAIN"], RetainHandler)


async def test_sentence_finalized_정상이면_행을_만들고_complete(  # ⑭
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    backend = SnapshotBackend(make_snapshot())

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert backend.snapshot_calls == 1
    rows = await fact_rows(engine)
    kinds = sorted((row["bank_type"], row["bank_id"], row["fact_type"]) for row in rows)
    assert kinds == [
        ("room", ROOM_ID, "VERDICT"),
        ("user", AUTHOR_ID, "SPEND"),
        ("user", AUTHOR_ID, "VERDICT"),
    ]
    by_kind = {(row["bank_type"], row["fact_type"]): row for row in rows}
    spend = by_kind[("user", "SPEND")]
    assert (spend["source_type"], spend["source_id"], spend["source_version"]) == (
        "POST",
        POST_ID,
        1,
    )
    assert spend["epistemic_type"] == "DB_RECORD"
    assert spend["payload"]["reason"] == "늦잠 자서 택시 탐"
    assert spend["scope"] == {"visibility": "ROOMS", "room_ids": [ROOM_ID]}
    snapshot = make_snapshot()
    assert snapshot.jury is not None
    assert spend["occurred_at"] == snapshot.created_at
    verdict = by_kind[("user", "VERDICT")]
    assert verdict["epistemic_type"] == "DB_RECORD"
    assert verdict["occurred_at"] == snapshot.jury.confirmed_at
    assert by_kind[("room", "VERDICT")]["occurred_at"] == snapshot.jury.confirmed_at
    assert (verdict["source_type"], verdict["source_id"]) == ("VERDICT", VERDICT_ID)
    assert verdict["payload"]["result"] == "guilty"
    assert verdict["payload"]["sentence"] == "oneDay"
    room = by_kind[("room", "VERDICT")]
    assert room["payload"] == {
        "post_id": POST_ID,
        "author_id": AUTHOR_ID,
        "category": "교통/택시",
        "result": "guilty",
        "guilty_ratio": 0.75,
        "intensity": "spicy",
    }
    assert room["scope"] == {"visibility": "ROOMS", "room_ids": [ROOM_ID]}
    assert await processed_count(engine) == 1


async def test_public_share_면_사용자_행_scope_가_PUBLIC(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    base = make_snapshot()
    audience = base.audience.model_dump() | {"public_share_enabled": True}
    backend = SnapshotBackend(make_snapshot(audience=audience))

    await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    rows = await fact_rows(engine)
    for row in rows:
        expected = "PUBLIC" if row["bank_type"] == "user" else "ROOMS"
        assert row["scope"]["visibility"] == expected


async def test_verdict_final_None_이면_행_0_complete(  # ⑮
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    backend = SnapshotBackend(make_snapshot(verdict_final=None))

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []


async def test_jury_None_이면_행_0_complete(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    backend = SnapshotBackend(make_snapshot(jury=None))

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []


async def test_snapshot_BACKEND_UNAVAILABLE_이면_재시도로_fail(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    backend = SnapshotBackend(unavailable=True)

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["last_error_code"] == "BACKEND_UNAVAILABLE"
    assert await fact_rows(engine) == []
    assert await processed_count(engine) == 0


async def test_comment_approved_통과하면_COMMENT_행(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    backend = SnapshotBackend(make_snapshot())

    job_id = await run_retain(
        engine=engine, jobs=jobs, enqueue=enqueue, backend=backend, event_type="comment.approved"
    )

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    rows = await fact_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert (row["bank_type"], row["bank_id"], row["fact_type"]) == ("room", ROOM_ID, "COMMENT")
    assert (row["source_type"], row["source_id"], row["source_version"]) == (
        "COMMENT",
        COMMENT_ID,
        1,
    )
    assert row["epistemic_type"] == "USER_CLAIM"
    assert row["payload"]["content"] == SAFE_COMMENT_CONTENT
    assert row["scope"] == {"visibility": "ROOMS", "room_ids": [ROOM_ID]}


async def test_comment_approved_안전_필터_탈락이면_행_0(  # ⑯
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, MATCHING_EPOCHS)
    unsafe = make_snapshot().comment.model_dump() | {  # type: ignore[union-attr]
        "content": "010-1234-5678 로 연락 주세요 택시 말고 지하철 타요"
    }
    backend = SnapshotBackend(make_snapshot(comment=unsafe))

    job_id = await run_retain(
        engine=engine, jobs=jobs, enqueue=enqueue, backend=backend, event_type="comment.approved"
    )

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []


async def test_comment_None_이면_행_0_complete(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    backend = SnapshotBackend(make_snapshot(comment=None))

    job_id = await run_retain(
        engine=engine, jobs=jobs, enqueue=enqueue, backend=backend, event_type="comment.approved"
    )

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []


async def test_epoch_불일치면_행_0(  # ⑰
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    await seed_epochs(engine, {f"user:{AUTHOR_ID}": 1, f"room:{ROOM_ID}": 2})
    backend = SnapshotBackend(make_snapshot())

    job_id = await run_retain(engine=engine, jobs=jobs, enqueue=enqueue, backend=backend)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await fact_rows(engine) == []
    assert await processed_count(engine) == 0
