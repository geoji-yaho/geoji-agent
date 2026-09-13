"""M2 수직 흐름(03 §4.2 `test_m2_vertical`, VF-03).

가짜 백엔드(`ASGITransport`) + 실제 Postgres + in-process 워커 런타임.

① SENTENCE: claim → begin → failed(AI_NOT_READY) → complete. 가짜 백엔드 FINAL/RULE +
   TEMPLATE_READY, TEXT_RETRY 예약 없음, `ai.jobs` 에 RETAIN(`sentence.finalized`) 1행
② PREPARE·RETAIN 은 백엔드를 부르지 않고 SUCCEEDED
③ TEXT_RETRY(FINAL 상태) 는 폴백을 유지한 채 SUCCEEDED
④ 백엔드 주소가 죽었으면 `fail(BACKEND_UNAVAILABLE, 5)` → QUEUED · available_at +5s
"""

from __future__ import annotations

import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.contracts.jobs import Job
from geoji_ai.workers.main import Worker
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN
from tests.integration.test_worker_runtime import make_settings, run_worker_until

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

VERDICT_ID = "v1"
POST_ID = "p1"


class TimedJobs:
    """claim·complete 시각을 기록하는 `JobsPort` 감싸개. 동작은 그대로 넘긴다."""

    def __init__(self, inner: PostgresJobs) -> None:
        self._inner = inner
        self.claimed_at: dict[str, float] = {}
        self.completed_at: dict[str, float] = {}

    async def claim(self, kinds: Sequence[str], worker_id: str) -> Job | None:
        job = await self._inner.claim(kinds, worker_id)
        if job is not None:
            self.claimed_at[job.id] = time.perf_counter()
        return job

    async def complete(self, job_id: str, worker_id: str, generation_id: str) -> bool:
        done = await self._inner.complete(job_id, worker_id, generation_id)
        self.completed_at[job_id] = time.perf_counter()
        return done

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _seed(fake_backend: FastAPI) -> Any:
    fake = fake_backend.state.fake
    fake.seed_verdict(
        VERDICT_ID,
        verdict_version=1,
        post_id=POST_ID,
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    return fake


def _status_is(fetch_job: FetchJob, job_id: str, *statuses: str) -> Callable[[], Awaitable[bool]]:
    async def _check() -> bool:
        return (await fetch_job(job_id))["status"] in statuses

    return _check


async def _retain_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT event_type, dedupe_key, payload, status FROM ai.jobs WHERE kind='RETAIN'")
        )
        return [dict(row) for row in result.mappings()]


async def _run_sentence(
    jobs: Any, enqueue: Enqueue, fetch_job: FetchJob, backend: BackendHttp
) -> str:
    job_id = await enqueue("SENTENCE", verdict_id=VERDICT_ID, verdict_version=1, post_id=POST_ID)
    worker = Worker(jobs, make_settings(WORKER_SLOTS={"SENTENCE": 1}), backend=backend)
    await run_worker_until(worker, _status_is(fetch_job, job_id, "SUCCEEDED", "FAILED"))
    return job_id


# --- ① SENTENCE ----------------------------------------------------------------


async def test_SENTENCE_스텁이_폴백_확정과_RETAIN_을_만든다(
    jobs: PostgresJobs,
    engine: AsyncEngine,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    fake_backend: FastAPI,
    backend_client: BackendHttp,
):
    fake = _seed(fake_backend)
    timed = TimedJobs(jobs)

    job_id = await _run_sentence(timed, enqueue, fetch_job, backend_client)

    row = await fetch_job(job_id)
    assert row["status"] == "SUCCEEDED"
    state = fake.verdicts[VERDICT_ID]
    assert (state.sentence_status, state.sentence_source, state.text_status) == (
        "FINAL",
        "RULE",
        "TEMPLATE_READY",
    )
    assert state.sentence == "oneDay"
    assert state.active_generation_id is None
    assert fake.text_retry_rounds == []
    assert [path.rsplit("/", 1)[-1] for _, path in fake.calls] == [
        "begin-generation",
        "generation-failed",
    ]

    retain = await _retain_rows(engine)
    assert len(retain) == 1
    assert retain[0]["event_type"] == "sentence.finalized"
    assert retain[0]["dedupe_key"] == f"retain:verdict:{VERDICT_ID}:1"
    assert retain[0]["status"] == "QUEUED"
    payload = retain[0]["payload"]
    assert payload == {
        "event": "sentence.finalized",
        "verdict_id": VERDICT_ID,
        "comment_id": None,
        "version": 1,
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_backend), base_url="http://fake"
    ) as client:
        view = (await client.get(f"/posts/{POST_ID}/verdict")).json()
    assert view["sentence_status"] == "FINAL"
    assert view["text_status"] == "TEMPLATE_READY"
    assert view["view"]["source"] == "TEMPLATE"

    elapsed_ms = (timed.completed_at[job_id] - timed.claimed_at[job_id]) * 1000
    # 03 §4.1 목표 < 500ms. 기록용이다(`-s` 로 본다).
    print(f"sentence_stub_claim_to_complete_ms={elapsed_ms:.1f}")


# --- ② PREPARE · RETAIN ----------------------------------------------------------


async def test_PREPARE_RETAIN_은_백엔드를_부르지_않고_SUCCEEDED(
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    fake_backend: FastAPI,
    backend_client: BackendHttp,
):
    fake = fake_backend.state.fake
    prepare_id = await enqueue("PREPARE", post_id=POST_ID, post_version=1, audience_version=1)
    retain_id = await enqueue("RETAIN", verdict_id="v9", comment_id=None, version=1)
    worker = Worker(
        jobs,
        make_settings(WORKER_SLOTS={"PREPARE": 1, "BACKGROUND": 1}),
        backend=backend_client,
    )

    async def both_done() -> bool:
        rows = [await fetch_job(prepare_id), await fetch_job(retain_id)]
        return all(row["status"] == "SUCCEEDED" for row in rows)

    await run_worker_until(worker, both_done)

    assert (await fetch_job(prepare_id))["status"] == "SUCCEEDED"
    assert (await fetch_job(retain_id))["status"] == "SUCCEEDED"
    assert fake.calls == []


# --- ③ TEXT_RETRY(FINAL) --------------------------------------------------------


async def test_TEXT_RETRY_는_FINAL_폴백을_유지하고_SUCCEEDED(
    jobs: PostgresJobs,
    engine: AsyncEngine,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    fake_backend: FastAPI,
    backend_client: BackendHttp,
):
    fake = _seed(fake_backend)
    await _run_sentence(jobs, enqueue, fetch_job, backend_client)
    assert fake.verdicts[VERDICT_ID].sentence_status == "FINAL"
    fake.calls.clear()

    retry_id = await enqueue("TEXT_RETRY", verdict_id=VERDICT_ID, verdict_version=1, round=1)
    worker = Worker(jobs, make_settings(WORKER_SLOTS={"BACKGROUND": 1}), backend=backend_client)
    await run_worker_until(worker, _status_is(fetch_job, retry_id, "SUCCEEDED", "FAILED"))

    assert (await fetch_job(retry_id))["status"] == "SUCCEEDED"
    state = fake.verdicts[VERDICT_ID]
    assert (state.sentence_status, state.sentence_source, state.text_status) == (
        "FINAL",
        "RULE",
        "TEMPLATE_READY",
    )
    assert state.sentence == "oneDay"
    assert state.text_version == 0
    assert fake.text_retry_rounds == []
    assert [path.rsplit("/", 1)[-1] for _, path in fake.calls] == [
        "begin-generation",
        "generation-failed",
    ]
    # RETAIN 은 최초 확정 때만이다.
    assert len(await _retain_rows(engine)) == 1


# --- ④ 백엔드 불가 ----------------------------------------------------------------


def _dead_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


async def test_백엔드가_죽었으면_BACKEND_UNAVAILABLE_로_5초_뒤_재시도(
    jobs: PostgresJobs, enqueue: Enqueue, fetch_job: FetchJob
):
    settings = make_settings(
        WORKER_SLOTS={"SENTENCE": 1},
        BACKEND_INTERNAL_URL=_dead_url(),
        SERVICE_AUTH_TOKEN=FAKE_SERVICE_TOKEN,
    )
    backend = BackendHttp(settings.BACKEND_INTERNAL_URL, FAKE_SERVICE_TOKEN)
    job_id = await enqueue("SENTENCE", verdict_id=VERDICT_ID, verdict_version=1, post_id=POST_ID)
    worker = Worker(jobs, settings, backend=backend)

    async def failed() -> bool:
        return (await fetch_job(job_id))["last_error_code"] is not None

    try:
        await run_worker_until(worker, failed, timeout=20.0)
    finally:
        await backend.aclose()

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["last_error_code"] == "BACKEND_UNAVAILABLE"
    assert row["attempts"] == 1
    assert row["owner_id"] is None
    # FAIL_SQL 은 같은 now() 로 updated_at 과 available_at(+retry_after_s) 을 쓴다.
    delta = (row["available_at"] - row["updated_at"]).total_seconds()
    assert abs(delta - 5) < 0.01
