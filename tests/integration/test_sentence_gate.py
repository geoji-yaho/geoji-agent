"""SENTENCE 게이트 재현(9/14 D-24, 10 §3 게이트·§6 마감 기준). 실제 Postgres + 가짜 백엔드.

가짜 백엔드 `confirm_verdict`·`run_sentence_gate` 가 백엔드 스케줄러 자리를 한다. 평결 확정 때 같은
post 의 PREPARE 가 `QUEUED`·`RUNNING` 이면 SENTENCE 를 보류하고, PREPARE 가 끝나거나
`confirmed_at + 30s` 에 닿으면 INSERT 한다. 마감 = INSERT 시각(DB now) + 10s.
시간은 `run_sentence_gate(now=...)` 로 주입한다(대기·폴링 없음).

① 전원 투표가 PREPARE 보다 빠름 → PREPARE 뒤 INSERT · `dossier_source=PREP`
② PREPARE 가 30초 넘게 멈춤 → 대기 해제 뒤 INSERT · MINIMAL
③ PREPARE 가 이미 끝남(FAILED·CANCELLED·SUCCEEDED) → 즉시 INSERT
④ PREPARE job 없음(다른 post 의 PREPARE 는 무관) → 즉시 INSERT
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.jobs import Job
from geoji_ai.workers.dispatch import handler_for
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, SENTENCE_GATE_WAIT, create_fake_backend
from tests.integration.test_graph_b import CONTEXT_OK
from tests.integration.test_graph_c_flow import (
    POST_ID,
    VERDICT_ID,
    WORKER_ID,
    Env,
    PlannedLLM,
    SpyBackend,
    _rows,
    _snapshot_data,
)
from tests.integration.test_llm_wiring import Capture
from tests.integration.test_retain_handler import seed_epochs

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

SENTENCE_DEADLINE = timedelta(seconds=10)


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "case-snapshot-sentence-gate.json"
    path.write_text(json.dumps(_snapshot_data(), ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
async def env(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    snapshot_path: Path,
) -> AsyncIterator[Env]:
    snapshot = CaseSnapshot.model_validate(_snapshot_data())
    await seed_epochs(engine, {pv.scope_key: pv.epoch for pv in snapshot.privacy_versions})
    app = create_fake_backend(jobs_engine=engine, snapshot_fixture=snapshot_path)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    http = BackendHttp("http://fake-backend", FAKE_SERVICE_TOKEN, client=client)
    try:
        yield Env(engine, jobs, enqueue, fetch_job, app, SpyBackend(http))
    finally:
        await http.aclose()


async def _sentence_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    return await _rows(
        engine,
        "SELECT CAST(id AS text) AS id, status, payload, created_at, deadline_at, dedupe_key "
        "FROM ai.jobs WHERE kind = 'SENTENCE' ORDER BY created_at",
    )


async def _claimed_prepare(env: Env) -> Job:
    job_id = await env.enqueue("PREPARE", post_id=POST_ID, post_version=1, audience_version=1)
    job = await env.jobs.claim(["PREPARE"], WORKER_ID)
    assert job is not None and job.id == job_id
    return job


def _assert_inserted_with_deadline(env: Env, rows: list[dict[str, Any]], inserted: list[str]):
    assert [row["id"] for row in rows] == inserted
    assert len(inserted) == 1
    row = rows[0]
    assert row["status"] == "QUEUED"
    assert row["payload"] == {"verdict_id": VERDICT_ID, "verdict_version": 1, "post_id": POST_ID}
    assert row["dedupe_key"] == f"sentence:{VERDICT_ID}:1"
    # 마감 = INSERT 시각 + 10s(같은 트랜잭션의 DB now()).
    assert row["deadline_at"] - row["created_at"] == SENTENCE_DEADLINE
    assert env.fake.verdicts[VERDICT_ID].deadline_at == row["deadline_at"]
    assert env.fake.verdicts[VERDICT_ID].sentence_status == "PENDING"


async def _run_sentence(env: Env, job_id: str, llm: Any) -> Capture:
    job = await env.jobs.claim(["SENTENCE"], WORKER_ID)
    assert job is not None and job.id == job_id
    capture = Capture()
    await SentenceHandler(capture)(job, env.ctx(job, llm))
    return capture


# --- ① PREPARE 뒤 INSERT ---------------------------------------------------------------


async def test_01_전원_투표가_PREPARE_보다_빠르면_PREPARE_가_끝난_뒤_넣고_준비_자료를_쓴다(
    env: Env,
):
    prepare = await _claimed_prepare(env)

    assert await env.fake.confirm_verdict(VERDICT_ID, post_id=POST_ID) is None
    assert await _sentence_rows(env.engine) == []
    assert env.fake.verdicts[VERDICT_ID].deadline_at is None  # 대기 중은 watchdog 대상 아님
    assert await env.fake.run_watchdog() == []
    assert await env.fake.run_sentence_gate() == []  # 아직 RUNNING

    await handler_for("PREPARE")(
        prepare, env.ctx(prepare, FakeLLM(outputs={"context": CONTEXT_OK}))
    )
    assert (await env.fetch_job(prepare.id))["status"] == "SUCCEEDED"

    inserted = await env.fake.run_sentence_gate()

    _assert_inserted_with_deadline(env, await _sentence_rows(env.engine), inserted)
    assert await env.fake.run_sentence_gate() == []  # 한 번만 넣는다
    prep = await _rows(
        env.engine, "SELECT status, CAST(dossier_id AS text) AS dossier_id FROM ai.trial_prep"
    )
    assert [row["status"] for row in prep] == ["COMPLETE"]

    llm = PlannedLLM()
    capture = await _run_sentence(env, inserted[0], llm)

    assert capture.last["dossier_source"] == "PREP"
    assert "context" not in llm.roles()
    assert [req.dossier_id for req in env.backend.finalized] == [prep[0]["dossier_id"]]
    assert (await env.fetch_job(inserted[0]))["status"] == "SUCCEEDED"


# --- ② 30초 대기 해제 --------------------------------------------------------------------


async def test_02_PREPARE_가_30초_넘게_멈추면_대기를_풀고_넣고_MINIMAL(env: Env):
    prepare = await _claimed_prepare(env)
    confirmed_at = datetime.now(UTC)
    assert env.fake.sentence_gate_wait == SENTENCE_GATE_WAIT == timedelta(seconds=30)

    assert (
        await env.fake.confirm_verdict(VERDICT_ID, post_id=POST_ID, confirmed_at=confirmed_at)
        is None
    )
    just_before = confirmed_at + SENTENCE_GATE_WAIT - timedelta(milliseconds=1)
    assert await env.fake.run_sentence_gate(now=just_before) == []

    inserted = await env.fake.run_sentence_gate(now=confirmed_at + SENTENCE_GATE_WAIT)

    _assert_inserted_with_deadline(env, await _sentence_rows(env.engine), inserted)
    assert (await env.fetch_job(prepare.id))["status"] == "RUNNING"

    llm = PlannedLLM(FakeLLM(outputs={"context": CONTEXT_OK}))
    capture = await _run_sentence(env, inserted[0], llm)

    assert capture.last["dossier_source"] == "MINIMAL"
    assert llm.roles()["context"] == 0
    assert await _rows(env.engine, "SELECT id FROM ai.trial_prep") == []


# --- ③ PREPARE 종료 → 즉시 --------------------------------------------------------------


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "SUCCEEDED"])
async def test_03_PREPARE_가_끝난_상태면_즉시_넣는다(env: Env, status: str):
    await env.enqueue("PREPARE", status=status, post_id=POST_ID, post_version=1, audience_version=1)

    job_id = await env.fake.confirm_verdict(VERDICT_ID, post_id=POST_ID)

    rows = await _sentence_rows(env.engine)
    assert job_id is not None
    _assert_inserted_with_deadline(env, rows, [job_id])


# --- ④ PREPARE 없음 → 즉시 ---------------------------------------------------------------


async def test_04_PREPARE_job_이_없으면_즉시_넣고_다른_post_의_PREPARE_는_무관(env: Env):
    await env.enqueue("PREPARE", post_id="post-other", post_version=1, audience_version=1)

    job_id = await env.fake.confirm_verdict(VERDICT_ID, post_id=POST_ID)

    rows = await _sentence_rows(env.engine)
    assert job_id is not None
    _assert_inserted_with_deadline(env, rows, [job_id])
