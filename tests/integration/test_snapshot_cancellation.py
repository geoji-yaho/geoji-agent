"""삭제 snapshot 취소의 실제 PostgreSQL 소유 CAS와 terminal 보존."""

from uuid import uuid4

import pytest
from sqlalchemy import text

PREPARE = {"post_id": "deleted-post", "post_version": 1, "audience_version": 1}


async def test_cancel_clears_lease_and_is_not_claimed_again(jobs, enqueue, fetch_job):
    job_id = await enqueue("PREPARE", **PREPARE)
    job = await jobs.claim(["PREPARE"], "owner")
    assert job is not None
    assert await jobs.cancel(job_id, "owner", job.generation_id, error_code="SNAPSHOT_NOT_FOUND")
    row = await fetch_job(job_id)
    assert row["status"] == "CANCELLED"
    assert row["last_error_code"] == "SNAPSHOT_NOT_FOUND"
    assert row["attempts"] == 1
    assert all(row[key] is None for key in ("owner_id", "generation_id", "lease_until"))
    assert await jobs.claim(["PREPARE"], "another") is None


@pytest.mark.parametrize("condition", ["wrong_owner", "stale_generation", "expired", "cancelled"])
async def test_cancel_never_overwrites_lost_ownership(condition, engine, jobs, enqueue, fetch_job):
    job_id = await enqueue("PREPARE", **PREPARE)
    job = await jobs.claim(["PREPARE"], "owner")
    assert job is not None
    worker, generation = "owner", job.generation_id
    if condition == "wrong_owner":
        worker = "another"
    elif condition == "stale_generation":
        generation = str(uuid4())
    else:
        update = (
            "lease_until=now()-interval '1 second'"
            if condition == "expired"
            else "status='CANCELLED',owner_id=NULL,generation_id=NULL,lease_until=NULL"
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(f"UPDATE ai.jobs SET {update} WHERE id=:id"), {"id": job.id}
            )
    before = await fetch_job(job_id)
    assert not await jobs.cancel(job_id, worker, generation, error_code="SNAPSHOT_NOT_FOUND")
    assert await fetch_job(job_id) == before
