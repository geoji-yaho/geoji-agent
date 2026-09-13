"""워커 런타임(02 §4.2 `test_worker_runtime`, §4.1 종료 행).

① 슬롯별 kinds 필터 ② 세마포어 8 초과 시 9번째 대기 ③ heartbeat 실패 → 핸들러
`CancelledError` 가 상위에 도달하고 결과 저장 없음 ④ 종료 → 진행 중 job `release`,
RUNNING 잔여 0 ⑤ `NotImplementedHandler` 가 `fail(NOT_IMPLEMENTED, 60)` 을 남긴다.

실제 Postgres 를 쓴다(`TEST_DATABASE_URL`). 모델은 부르지 않는다.
이 기계는 Windows 라 SIGTERM 을 보낼 수 없어 `request_shutdown()` 을 직접 부른다
(02 §3.3 종료 절차는 같다. POSIX 에서는 `loop.add_signal_handler` 가 이것을 부른다).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.contracts.jobs import Job
from geoji_ai.core.config import Settings
from geoji_ai.workers import dispatch
from geoji_ai.workers.dispatch import SLOT_KINDS, HandlerContext
from geoji_ai.workers.main import Worker, make_worker_id

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]


def make_settings(**over: Any) -> Settings:
    """테스트용 설정. 폴링만 줄이고 나머지는 계획서 기본값이다."""
    base: dict[str, Any] = {
        "OPENAI_API_KEY": "sk-test",
        "XAI_API_KEY": "xai-test",
        "WORKER_POLL_MS": 50,
    }
    base.update(over)
    return Settings(_env_file=None, **base)


async def run_worker_until(
    worker: Worker, done: Callable[[], Any], *, timeout: float = 15.0
) -> None:
    """`done()` 이 참이 될 때까지 워커를 돌리고 종료한다. `done` 은 동기·비동기 둘 다 된다."""
    task = asyncio.create_task(worker.run())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while loop.time() < deadline:
            result = done()
            if inspect.isawaitable(result):
                result = await result
            if result:
                break
            await asyncio.sleep(0.05)
    finally:
        worker.request_shutdown()
        await asyncio.wait_for(task, timeout=timeout)


async def _running_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM ai.jobs WHERE status = 'RUNNING'"))
        return int(result.scalar_one())


async def _prepare_payload_job(enqueue: Enqueue, post_id: str = "p1") -> str:
    return await enqueue("PREPARE", post_id=post_id, post_version=1, audience_version=1)


# --- worker_id (02 §3.3 소유권) ----------------------------------------------


def test_worker_id_는_host_pid_slot():
    worker_id = make_worker_id("SENTENCE")
    host, pid, slot = worker_id.split(":")
    assert host == socket.gethostname()
    assert pid == str(os.getpid())
    assert slot == "SENTENCE"


# --- ① 슬롯별 kinds 필터 -----------------------------------------------------


async def test_슬롯은_자기_kinds_만_집는다(
    jobs: PostgresJobs, enqueue: Enqueue, fetch_job: FetchJob
):
    assert SLOT_KINDS["SENTENCE"] == ("SENTENCE",)
    assert SLOT_KINDS["BACKGROUND"] == ("TEXT_RETRY", "RETAIN")
    job_id = await _prepare_payload_job(enqueue)

    # SENTENCE 슬롯만 띄운다. PREPARE 는 건드리지 않는다.
    sentence_only = Worker(jobs, make_settings(WORKER_SLOTS={"SENTENCE": 2}))
    task = asyncio.create_task(sentence_only.run())
    await asyncio.sleep(0.5)  # 폴링 50ms + jitter → 여러 번 돈다
    sentence_only.request_shutdown()
    await asyncio.wait_for(task, timeout=15)

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["attempts"] == 0
    assert row["owner_id"] is None

    # PREPARE 슬롯을 띄우면 같은 job 을 집는다.
    prepare_only = Worker(jobs, make_settings(WORKER_SLOTS={"PREPARE": 1}))

    async def handled() -> bool:
        return (await fetch_job(job_id))["attempts"] == 1

    await run_worker_until(prepare_only, handled)
    row = await fetch_job(job_id)
    assert row["attempts"] == 1
    assert row["last_error_code"] == "NOT_IMPLEMENTED"


# --- ② 세마포어 ---------------------------------------------------------------


async def test_세마포어는_프로세스_전역_하나고_8_초과는_대기한다(
    jobs: PostgresJobs,
    enqueue: Enqueue,
    monkeypatch: pytest.MonkeyPatch,
):
    # 작업 2 의 스텁은 모델을 부르지 않는다. 그래서 세마포어를 실제로 점유하는 경로가
    # 없다 — 핸들러가 `ctx.semaphore` 로 받는 객체가 슬롯 전체에서 하나인지를 본다.
    settings = make_settings(WORKER_SLOTS={"PREPARE": 1, "BACKGROUND": 1})
    assert settings.MODEL_CONCURRENCY_LIMIT == 8
    worker = Worker(jobs, settings)
    seen: list[asyncio.Semaphore] = []

    async def capture(job: Job, ctx: HandlerContext) -> None:
        seen.append(ctx.semaphore)
        await ctx.jobs.fail(
            job.id,
            ctx.worker_id,
            ctx.generation_id,
            error_code="NOT_IMPLEMENTED",
            retry_after_s=60,
        )

    monkeypatch.setitem(dispatch.HANDLERS, "PREPARE", capture)
    monkeypatch.setitem(dispatch.HANDLERS, "RETAIN", capture)
    await _prepare_payload_job(enqueue)
    await enqueue("RETAIN", verdict_id="v1", comment_id=None, version=1)

    await run_worker_until(worker, lambda: len(seen) >= 2)

    assert len(seen) >= 2
    # 슬롯이 달라도 세마포어는 하나다.
    assert all(sem is worker.semaphore for sem in seen)

    semaphore = worker.semaphore
    for _ in range(settings.MODEL_CONCURRENCY_LIMIT):
        await asyncio.wait_for(semaphore.acquire(), timeout=1)
    ninth = asyncio.create_task(semaphore.acquire())
    await asyncio.sleep(0.1)
    assert not ninth.done()  # 9번째는 대기한다
    semaphore.release()
    await asyncio.wait_for(ninth, timeout=1)
    for _ in range(settings.MODEL_CONCURRENCY_LIMIT):
        semaphore.release()


# --- ③ heartbeat 실패 → 핸들러 취소 ------------------------------------------


async def test_heartbeat_실패는_핸들러를_취소하고_결과를_남기지_않는다(
    make_jobs: Callable[..., PostgresJobs],
    enqueue: Enqueue,
    fetch_job: FetchJob,
    monkeypatch: pytest.MonkeyPatch,
):
    # lease 0.5초, heartbeat 1초 → 첫 heartbeat 때 이미 만료라 0행이다.
    jobs = make_jobs(lease_s=0.5)
    worker = Worker(jobs, make_settings(HEARTBEAT_SECONDS=1))
    saved: list[str] = []

    async def slow(job: Job, ctx: HandlerContext) -> None:
        await asyncio.sleep(30)
        saved.append(job.id)
        await ctx.jobs.complete(job.id, ctx.worker_id, ctx.generation_id)

    monkeypatch.setitem(dispatch.HANDLERS, "PREPARE", slow)
    job_id = await _prepare_payload_job(enqueue)

    worker_id = make_worker_id("PREPARE")
    job = await jobs.claim(("PREPARE",), worker_id)
    assert job is not None

    # 취소가 상위로 전파된다.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker.run_job(job, worker_id), timeout=10)

    assert saved == []
    row = await fetch_job(job_id)
    # 결과가 저장되지 않았다. 행은 lease 만료 상태 그대로이고 reaper 가 회수한다.
    assert row["status"] == "RUNNING"
    assert row["last_error_code"] is None


# --- ④ 종료 → release, RUNNING 잔여 0 ----------------------------------------


async def test_종료하면_진행_중_job_이_release_된다(
    jobs: PostgresJobs,
    engine: AsyncEngine,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = make_settings(WORKER_SLOTS={"PREPARE": 1}, WORKER_SHUTDOWN_DEADLINE_SECONDS=1)
    worker = Worker(jobs, settings)
    started = asyncio.Event()

    async def slow(job: Job, ctx: HandlerContext) -> None:
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setitem(dispatch.HANDLERS, "PREPARE", slow)
    job_id = await _prepare_payload_job(enqueue)

    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(started.wait(), timeout=10)
    assert await _running_count(engine) == 1

    # Windows 라 SIGTERM 대신 이벤트를 직접 세운다. 뒤 절차는 같다.
    worker.request_shutdown()
    await asyncio.wait_for(task, timeout=15)

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["attempts"] == 0  # release 는 attempts −1
    assert row["owner_id"] is None
    assert row["generation_id"] is None
    assert row["lease_until"] is None
    assert await _running_count(engine) == 0


# --- ⑤ 스텁 핸들러 -----------------------------------------------------------


async def test_스텁_핸들러가_NOT_IMPLEMENTED_로_되돌린다(
    jobs: PostgresJobs, enqueue: Enqueue, fetch_job: FetchJob
):
    worker = Worker(jobs, make_settings(WORKER_SLOTS={"PREPARE": 1}))
    job_id = await _prepare_payload_job(enqueue)

    async def failed() -> bool:
        return (await fetch_job(job_id))["last_error_code"] is not None

    await run_worker_until(worker, failed)

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["last_error_code"] == "NOT_IMPLEMENTED"
    assert row["attempts"] == 1
    assert row["owner_id"] is None
    # retry_after_s=60 → available_at 은 60초 뒤다.
    delta = (row["available_at"] - datetime.now(UTC)).total_seconds()
    assert 50 <= delta <= 61


async def test_네_kind_모두_스텁으로_처리된다(
    jobs: PostgresJobs, engine: AsyncEngine, enqueue: Enqueue, fetch_job: FetchJob
):
    # 기본 슬롯(SENTENCE 2 · PREPARE 1 · BACKGROUND 1)으로 4 kind 를 다 집는다.
    worker = Worker(jobs, make_settings())
    ids = {
        "PREPARE": await _prepare_payload_job(enqueue),
        "SENTENCE": await enqueue("SENTENCE", verdict_id="v1", verdict_version=1, post_id="p1"),
        "TEXT_RETRY": await enqueue("TEXT_RETRY", verdict_id="v1", verdict_version=1, round=1),
        "RETAIN": await enqueue("RETAIN", verdict_id="v2", comment_id=None, version=1),
    }

    async def all_failed() -> bool:
        rows = [await fetch_job(job_id) for job_id in ids.values()]
        return all(row["last_error_code"] is not None for row in rows)

    await run_worker_until(worker, all_failed)

    for kind, job_id in ids.items():
        row = await fetch_job(job_id)
        assert row["last_error_code"] == "NOT_IMPLEMENTED", kind
        assert row["attempts"] == 1, kind
        # TEXT_RETRY 는 max_attempts=1 이라 첫 실패에서 FAILED 다(02 §3.4).
        assert row["status"] == ("FAILED" if kind == "TEXT_RETRY" else "QUEUED"), kind
    assert await _running_count(engine) == 0
