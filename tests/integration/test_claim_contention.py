"""claim 경합·정렬·마감·지연 (02 §4.1 "claim 중복 0", §4.2 `test_claim_contention`)."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CLAIM_LOOP = Path(__file__).with_name("_claim_loop.py")

JOB_COUNT = 200
LOOP_TIMEOUT_S = 180
LATENCY_SAMPLES = 100
LATENCY_TARGET_MS = 350.0  # 02 §4.1 목표. 이 테스트는 이 값으로 실패하지 않는다(기록만).

_PREPARE = {"post_version": 1, "audience_version": 1}
_SENTENCE = {"verdict_version": 1, "post_id": "p1"}


def _run_claim_loop(worker_id: str, url: str) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["TEST_DATABASE_URL"] = url
    return subprocess.Popen(
        [sys.executable, str(_CLAIM_LOOP), worker_id, str(JOB_COUNT * 2)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


async def test_두_프로세스가_동시에_claim_해도_job_당_generation_은_하나다(
    engine, enqueue, test_database_url
):
    for index in range(JOB_COUNT):
        await enqueue("PREPARE", post_id=f"p{index}", **_PREPARE)

    processes = [_run_claim_loop(f"loop{n}", test_database_url) for n in (1, 2)]
    claimed: list[dict[str, str]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=LOOP_TIMEOUT_S)
        assert process.returncode == 0, stderr
        claimed.extend(json.loads(line) for line in stdout.splitlines() if line.strip())

    job_ids = [entry["job_id"] for entry in claimed]
    # 같은 job 을 둘이 RUNNING 으로 잡은 횟수 0 → job_id 가 전부 다르다.
    assert len(job_ids) == JOB_COUNT
    assert len(set(job_ids)) == JOB_COUNT
    assert len({entry["generation_id"] for entry in claimed}) == JOB_COUNT

    async with AsyncSession(engine) as session:
        rows = (
            await session.execute(text("SELECT status, count(*) FROM ai.jobs GROUP BY status"))
        ).all()
        attempted = (
            await session.execute(text("SELECT count(*) FROM ai.jobs WHERE attempts <> 1"))
        ).scalar_one()
    assert dict(rows) == {"RUNNING": JOB_COUNT}
    assert attempted == 0

    per_worker = Counter(entry["worker_id"] for entry in claimed)
    print(f"[02 §4.1] claim 중복 0, job {JOB_COUNT}개, 프로세스별 {dict(per_worker)}")


async def test_claim_은_priority_available_at_created_at_순서로_나온다(jobs, enqueue):
    now = datetime.now(UTC)
    older_high = await enqueue(
        "PREPARE", priority=100, available_at=now - timedelta(seconds=20), post_id="a", **_PREPARE
    )
    newer_high = await enqueue(
        "PREPARE", priority=100, available_at=now - timedelta(seconds=10), post_id="b", **_PREPARE
    )
    middle = await enqueue(
        "PREPARE", priority=50, available_at=now - timedelta(seconds=5), post_id="c", **_PREPARE
    )
    low = await enqueue(
        "PREPARE", priority=10, available_at=now - timedelta(seconds=30), post_id="d", **_PREPARE
    )

    order: list[str] = []
    while (job := await jobs.claim(["PREPARE"], "w1")) is not None:
        order.append(job.id)

    assert order == [older_high, newer_high, middle, low]


async def test_마감이_지난_SENTENCE_는_claim_되지_않는다(jobs, enqueue, fetch_job):
    now = datetime.now(UTC)
    expired = await enqueue(
        "SENTENCE", deadline_at=now - timedelta(seconds=1), verdict_id="v1", **_SENTENCE
    )
    alive = await enqueue(
        "SENTENCE", deadline_at=now + timedelta(seconds=30), verdict_id="v2", **_SENTENCE
    )

    job = await jobs.claim(["SENTENCE"], "w1")
    assert job is not None
    assert job.id == alive
    assert await jobs.claim(["SENTENCE"], "w1") is None

    row = await fetch_job(expired)
    assert row["status"] == "QUEUED"
    assert row["attempts"] == 0


async def test_claim_지연을_100회_재어_p95_를_기록한다(jobs, enqueue):
    samples: list[float] = []
    for index in range(LATENCY_SAMPLES):
        started = time.perf_counter()
        await enqueue("PREPARE", post_id=f"lat{index}", **_PREPARE)
        job = await jobs.claim(["PREPARE"], "w1")
        samples.append((time.perf_counter() - started) * 1000.0)
        assert job is not None

    samples.sort()
    p50 = samples[math.ceil(len(samples) * 0.50) - 1]
    p95 = samples[math.ceil(len(samples) * 0.95) - 1]
    verdict = "이내" if p95 <= LATENCY_TARGET_MS else "초과"
    print(
        f"[02 §4.1] claim 지연 n={len(samples)} "
        f"p50={p50:.1f}ms p95={p95:.1f}ms max={samples[-1]:.1f}ms "
        f"(목표 {LATENCY_TARGET_MS:.0f}ms {verdict}, 기록만)"
    )
    assert len(samples) == LATENCY_SAMPLES
