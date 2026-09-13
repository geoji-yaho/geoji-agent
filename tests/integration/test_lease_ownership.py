"""lease 소유권과 reaper 회수 (02 §4.1 "stale 저장 0", §4.2 `test_lease_ownership`).

`lease_s=1` 로 만료를 만든다. 시계를 조작하지 않는다.
"""

from __future__ import annotations

import asyncio

from geoji_ai.adapters.postgres_jobs import reap

_PREPARE = {"post_id": "p1", "post_version": 1, "audience_version": 1}


async def test_lease_만료_뒤_이전_owner_의_heartbeat_는_0행이다(make_jobs, enqueue):
    jobs = make_jobs(lease_s=1)
    job_id = await enqueue("PREPARE", **_PREPARE)

    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None
    assert job.id == job_id
    assert job.status == "RUNNING"
    assert job.generation_id is not None

    await asyncio.sleep(1.2)

    assert await jobs.heartbeat(job.id, "w1", job.generation_id) is False


async def test_reaper_가_PREPARE_를_QUEUED_로_회수한다(engine, make_jobs, enqueue, fetch_job):
    jobs = make_jobs(lease_s=1)
    job_id = await enqueue("PREPARE", **_PREPARE)
    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None

    await asyncio.sleep(1.2)
    assert await reap(engine) == 1

    row = await fetch_job(job_id)
    assert row["status"] == "QUEUED"
    assert row["last_error_code"] == "LEASE_EXPIRED"
    assert row["owner_id"] is None
    assert row["generation_id"] is None
    assert row["lease_until"] is None
    # 회수는 attempts 를 되돌리지 않는다(release 만 되돌린다).
    assert row["attempts"] == 1


async def test_회수_뒤_새_claim_은_새_generation_을_받는다(engine, make_jobs, enqueue):
    jobs = make_jobs(lease_s=1)
    await enqueue("PREPARE", **_PREPARE)
    first = await jobs.claim(["PREPARE"], "w1")
    assert first is not None

    await asyncio.sleep(1.2)
    assert await reap(engine) == 1

    second = await jobs.claim(["PREPARE"], "w2")
    assert second is not None
    assert second.id == first.id
    assert second.generation_id != first.generation_id
    assert second.owner_id == "w2"
    assert second.attempts == 2


async def test_이전_generation_의_complete_는_거부되고_행은_그대로다(
    engine, make_jobs, enqueue, fetch_job
):
    jobs = make_jobs(lease_s=1)
    job_id = await enqueue("PREPARE", **_PREPARE)
    first = await jobs.claim(["PREPARE"], "w1")
    assert first is not None

    await asyncio.sleep(1.2)
    assert await reap(engine) == 1
    second = await jobs.claim(["PREPARE"], "w2")
    assert second is not None

    before = await fetch_job(job_id)
    assert await jobs.complete(job_id, "w1", first.generation_id) is False
    after = await fetch_job(job_id)

    assert after["status"] == "RUNNING"
    assert after == before


async def test_heartbeat_성공은_lease_를_실제로_연장한다(make_jobs, enqueue, fetch_job):
    """실패(0행)만 보면 lease 를 늘리지 않아도 통합 테스트가 전부 통과한다.

    §3.2 표의 heartbeat SET 이 `lease_until` 을 미는 것을 직접 본다.
    """
    jobs = make_jobs(lease_s=3)
    await enqueue("PREPARE", **_PREPARE)
    job = await jobs.claim(["PREPARE"], "w1")
    assert job is not None
    assert job.generation_id is not None
    before = await fetch_job(job.id)

    await asyncio.sleep(1.0)
    assert await jobs.heartbeat(job.id, "w1", job.generation_id) is True

    after = await fetch_job(job.id)
    assert after["lease_until"] > before["lease_until"]
    assert after["status"] == "RUNNING"
    # 소유권은 그대로다. heartbeat 는 lease 만 민다.
    assert after["owner_id"] == before["owner_id"]
    assert after["generation_id"] == before["generation_id"]
    assert after["attempts"] == before["attempts"]
    # 4동작도 `updated_at` 을 찍는다(claim·reaper 원문과 같게).
    assert after["updated_at"] > before["updated_at"]
