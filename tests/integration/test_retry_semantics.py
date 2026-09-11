"""재시도·반납 의미 (02 §3.2 동작별 SET 표, §3.5 reaper, §4.2 `test_retry_semantics`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from geoji_ai.adapters.postgres_jobs import reap

_PREPARE = {"post_id": "p1", "post_version": 1, "audience_version": 1}
_SENTENCE = {"verdict_id": "v1", "verdict_version": 1, "post_id": "p1"}
_TEXT_RETRY = {"verdict_id": "v1", "verdict_version": 1, "round": 1}


async def test_fail_은_QUEUED_로_되돌리고_available_at_을_미룬다(jobs, enqueue, fetch_job):
    job_id = await enqueue("PREPARE", **_PREPARE)
    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None
    assert job.attempts == 1

    assert await jobs.fail(job.id, "w1", job.generation_id, "NOT_IMPLEMENTED", 5) is True

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    # attempts 는 claim 때 +1 됐다. fail 은 건드리지 않는다.
    assert row["attempts"] == 1
    assert row["last_error_code"] == "NOT_IMPLEMENTED"
    assert row["owner_id"] is None
    assert row["generation_id"] is None
    assert row["lease_until"] is None
    delay = (row["available_at"] - datetime.now(UTC)).total_seconds()
    assert 3.0 <= delay <= 5.5


async def test_retry_after_s_가_없으면_바로_다시_claim_된다(jobs, enqueue):
    await enqueue("PREPARE", **_PREPARE)
    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None
    assert await jobs.fail(job.id, "w1", job.generation_id, "NOT_IMPLEMENTED", None) is True

    again = await jobs.claim(["PREPARE"], "w1")
    assert again is not None
    assert again.attempts == 2


async def test_max_attempts_에_닿으면_FAILED_이고_다시_claim_되지_않는다(jobs, enqueue, fetch_job):
    job_id = await enqueue("PREPARE", max_attempts=1, **_PREPARE)
    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None
    assert job.attempts == 1

    assert await jobs.fail(job.id, "w1", job.generation_id, "NOT_IMPLEMENTED", None) is True

    row = await fetch_job(job_id)
    assert row["status"] == "FAILED"
    assert await jobs.claim(["PREPARE"], "w2") is None


async def test_attempts_를_소진한_QUEUED_행은_claim_되지_않는다(jobs, enqueue):
    await enqueue("PREPARE", max_attempts=2, attempts=2, **_PREPARE)
    assert await jobs.claim(["PREPARE"], "w1") is None


async def test_TEXT_RETRY_는_reaper_에서_FAILED_가_된다(engine, make_jobs, enqueue, fetch_job):
    jobs = make_jobs(lease_s=1)
    job_id = await enqueue("TEXT_RETRY", **_TEXT_RETRY)
    job = await jobs.claim(["TEXT_RETRY"], "w1")
    assert job is not None

    await asyncio.sleep(1.2)
    assert await reap(engine) == 1

    row = await fetch_job(job_id)
    assert row["status"] == "FAILED"
    assert row["last_error_code"] == "LEASE_EXPIRED"
    assert row["owner_id"] is None
    assert row["generation_id"] is None
    assert row["lease_until"] is None


async def test_release_는_attempts_를_되돌리고_QUEUED_로_반납한다(jobs, enqueue, fetch_job):
    job_id = await enqueue("PREPARE", **_PREPARE)
    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None
    assert job.attempts == 1

    assert await jobs.release(job.id, "w1", job.generation_id) is True

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["attempts"] == 0
    assert row["owner_id"] is None
    assert row["generation_id"] is None
    assert row["lease_until"] is None


async def test_마감이_지난_SENTENCE_는_reaper_에서_CANCELLED_가_된다(
    engine, make_jobs, enqueue, fetch_job
):
    jobs = make_jobs(lease_s=1)
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    job_id = await enqueue("SENTENCE", deadline_at=deadline, **_SENTENCE)
    job = await jobs.claim(["SENTENCE"], "w1")
    assert job is not None

    await asyncio.sleep(2.3)
    assert await reap(engine) == 1

    row = await fetch_job(job_id)
    assert row["status"] == "CANCELLED"
    assert row["last_error_code"] == "LEASE_EXPIRED"
    assert row["owner_id"] is None
    assert row["generation_id"] is None
    assert row["lease_until"] is None
