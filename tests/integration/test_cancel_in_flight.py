"""삭제·철회·탈퇴 시 진행 중 작업 중단(9/14 D-26, 10 §8·§15.5). 실제 Postgres + 가짜 백엔드.

가짜 백엔드 `invalidate_post` 가 무효화 트랜잭션을 재현한다: scope epoch +1 과 그 post 의
`QUEUED`·`RUNNING` PREPARE·SENTENCE·TEXT_RETRY → `CANCELLED`. 워커는 heartbeat 소유권 조건
(`status='RUNNING'`)이 깨져 `lease_lost` → 핸들러 취소 → 게이트웨이가 원장 행을 `UNKNOWN` 으로
닫는다.

⑤ PREPARE 조서 호출 중 삭제 → 핸들러 취소 · 이후 모델 호출 0 · `trial_prep` 0 · 원장 RESERVED 0
⑤b epoch 가 이미 바뀐 PREPARE → 조서 호출 직전 확인에서 멈춤 · 원장 행 0 · `EVIDENCE_INVALIDATED`
⑤c 드립 호출에서 무효 → 그래프 B 가 삼키지 않음 · 드립 저장 0 · `EVIDENCE_INVALIDATED`
⑥ SENTENCE 서기 호출 중 삭제 → 핸들러 취소 · finalize 0 · 원장 RESERVED 0

heartbeat 는 `HEARTBEAT_SECONDS=1`(설정 최소 단위)이라 취소까지 약 1초 걸린다. 테스트는 이벤트를
기다리고 상한만 둔다(sleep 폴링 없음).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.fake_llm import FakeCall, FakeLLM
from geoji_ai.adapters.llm_router import RoleRoutedLLM, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.core.config import Settings
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.ports.llm import LLMResult
from geoji_ai.ports.preparation import EvidenceInvalidated
from geoji_ai.workers.main import Worker, make_worker_id
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration.test_graph_b import CONTEXT_OK
from tests.integration.test_graph_c_flow import (
    POST_ID,
    ROOM_B,
    VERDICT_ID,
    Env,
    SpyBackend,
    _rows,
    _snapshot_data,
)
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

#: 취소를 기다리는 상한. heartbeat 1초 + 원장 닫기.
CANCEL_WAIT_S = 10.0


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "case-snapshot-cancel.json"
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


class BlockingLLM:
    """`block_role` 호출에 들어오면 `entered` 를 세우고 취소될 때까지 끝나지 않는다."""

    def __init__(self, inner: FakeLLM, block_role: str) -> None:
        self.inner = inner
        self.block_role = block_role
        self.entered = asyncio.Event()
        self._never = asyncio.Event()
        self.roles: list[str] = []

    async def structured_call(
        self,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        self.roles.append(role)
        if role == self.block_role:
            self.entered.set()
            await self._never.wait()
        return await self.inner.structured_call(
            role=role,
            messages=messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )


class RecordingLLM(FakeLLM):
    @property
    def roles(self) -> list[str]:
        return [call.role for call in self.calls]


def _gateway(engine: AsyncEngine, settings: Settings, *, judgment: Any, writer: Any) -> LLMGateway:
    """운영 조립(`run_worker`)과 같은 모양. 벤더 자리에 가짜를 꽂는다."""
    router = RoleRoutedLLM(
        {"openai": judgment, "xai": writer},
        models={"openai": settings.MODEL_JUDGMENT, "xai": settings.MODEL_WRITER},
    )
    return LLMGateway(
        router,
        PostgresCallLedger(engine),
        VendorHealth(),
        price_for,
        stale_scopes=PostgresPreparation(engine).stale_scopes,
    )


def _worker(env: Env, settings: Settings, gateway: LLMGateway) -> Worker:
    return Worker(
        env.jobs,
        settings,
        backend=env.backend,  # type: ignore[arg-type]
        llm=gateway,
        preparation=PostgresPreparation(env.engine),
    )


async def _ledger(engine: AsyncEngine) -> list[tuple[str, str]]:
    rows = await _rows(engine, "SELECT node, status FROM ai.llm_calls ORDER BY node, call_index")
    return [(row["node"], row["status"]) for row in rows]


async def _count(engine: AsyncEngine, table: str) -> int:
    return len(await _rows(engine, f"SELECT 1 FROM {table}"))


async def _delete_while_blocked(
    env: Env, running: asyncio.Task[None], blocker: BlockingLLM
) -> list[str]:
    await asyncio.wait_for(blocker.entered.wait(), timeout=CANCEL_WAIT_S)
    cancelled = await env.fake.invalidate_post(POST_ID, scope_keys=[f"room:{ROOM_B}"])
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=CANCEL_WAIT_S)
    return cancelled


# --- ⑤ PREPARE 진행 중 삭제 ---------------------------------------------------------------


async def test_05_PREPARE_조서_호출_중_삭제되면_핸들러가_취소되고_저장_0_원장_RESERVED_0(env: Env):
    settings = make_settings(HEARTBEAT_SECONDS=1)
    judgment = BlockingLLM(FakeLLM(outputs={"context": CONTEXT_OK}), "context")
    writer = RecordingLLM()
    worker = _worker(
        env, settings, _gateway(env.engine, settings, judgment=judgment, writer=writer)
    )
    job_id = await env.enqueue("PREPARE", post_id=POST_ID, post_version=1, audience_version=1)
    worker_id = make_worker_id("PREPARE")
    job = await env.jobs.claim(["PREPARE"], worker_id)
    assert job is not None and job.id == job_id

    running = asyncio.create_task(worker.run_job(job, worker_id))
    cancelled = await _delete_while_blocked(env, running, judgment)

    assert cancelled == [job_id]
    row = await env.fetch_job(job_id)
    assert row["status"] == "CANCELLED"
    assert row["owner_id"] is None
    # 이후 모델 호출 0: 조서 1회(막힌 것)뿐, 드립 0.
    assert judgment.roles == ["context"]
    assert writer.roles == []
    assert await _count(env.engine, "ai.trial_prep") == 0
    assert await _count(env.engine, "ai.dossiers") == 0
    # 원장: 막힌 조서 호출은 발송 여부를 알 수 없어 UNKNOWN 으로 닫힌다(06 §3.2). RESERVED 0.
    assert await _ledger(env.engine) == [("context", "UNKNOWN")]
    epochs = await _rows(
        env.engine, "SELECT epoch FROM ai.privacy_epochs WHERE scope_key = :k", k=f"room:{ROOM_B}"
    )
    assert [row["epoch"] for row in epochs] == [2]


async def test_05b_epoch_가_이미_바뀐_PREPARE_는_조서를_부르지_않고_EVIDENCE_INVALIDATED(env: Env):
    await seed_epochs(env.engine, {f"room:{ROOM_B}": 2})
    settings = make_settings()
    judgment = RecordingLLM(outputs={"context": CONTEXT_OK})
    writer = RecordingLLM()
    gateway = _gateway(env.engine, settings, judgment=judgment, writer=writer)

    job_id = await env.prepare(gateway)

    row = await env.fetch_job(job_id)
    assert row["last_error_code"] == "EVIDENCE_INVALIDATED"
    # 이 소유권은 끝난다(owner NULL). attempts 가 남아 QUEUED — 저장 직전 epoch 불일치
    # (test_graph_b ⑤)와 같은 `fail` 경로다. 다음 시도는 새 epoch 스냅샷으로 다시 만든다.
    assert row["status"] == "QUEUED"
    assert row["owner_id"] is None
    assert judgment.roles == []
    assert writer.roles == []
    assert await _ledger(env.engine) == []
    assert await _count(env.engine, "ai.trial_prep") == 0
    assert await _count(env.engine, "ai.dossiers") == 0


class InvalidatedBanterLLM(RecordingLLM):
    """조서는 정상, 드립 호출에서 게이트웨이가 epoch 불일치를 올린 것처럼 `EvidenceInvalidated`."""

    async def structured_call(self, *, role: Any, **kwargs: Any) -> LLMResult:
        if role == "banter":
            self.calls.append(FakeCall(role=role, scenario=self.scenario, **kwargs))
            raise EvidenceInvalidated([f"room:{ROOM_B}"])
        return await super().structured_call(role=role, **kwargs)


async def test_05c_드립_호출에서_무효를_받으면_삼키지_않고_EVIDENCE_INVALIDATED_드립_저장_0(
    env: Env,
):
    llm = InvalidatedBanterLLM(outputs={"context": CONTEXT_OK})

    job_id = await env.prepare(llm)

    row = await env.fetch_job(job_id)
    assert row["last_error_code"] == "EVIDENCE_INVALIDATED"
    assert row["status"] == "QUEUED"
    assert "banter" in llm.roles
    # 조서는 드립 전에 저장됐다. 드립은 채워지지 않고 COMPLETE 로 넘어가지 않는다.
    rows = await _rows(env.engine, "SELECT status, banter_json FROM ai.trial_prep")
    assert [(r["status"], r["banter_json"]) for r in rows] == [("DOSSIER_READY", None)]


# --- ⑥ SENTENCE 서기 중 삭제 ----------------------------------------------------------------


async def test_06_SENTENCE_서기_호출_중_삭제되면_핸들러가_취소되고_finalize_0(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))  # 원장 없는 경로로 prep
    settings = make_settings(HEARTBEAT_SECONDS=1)
    judgment = RecordingLLM()
    writer = BlockingLLM(FakeLLM(), "writer")
    worker = _worker(
        env, settings, _gateway(env.engine, settings, judgment=judgment, writer=writer)
    )
    job_id = await env.enqueue(
        "SENTENCE", verdict_id=VERDICT_ID, verdict_version=1, post_id=POST_ID
    )
    worker_id = make_worker_id("SENTENCE")
    job = await env.jobs.claim(["SENTENCE"], worker_id)
    assert job is not None and job.id == job_id
    env.fake.seed_verdict(
        VERDICT_ID,
        verdict_version=1,
        post_id=POST_ID,
        deadline_at=job.updated_at + timedelta(seconds=60),
    )

    running = asyncio.create_task(worker.run_job(job, worker_id))
    cancelled = await _delete_while_blocked(env, running, writer)

    assert cancelled == [job_id]
    assert (await env.fetch_job(job_id))["status"] == "CANCELLED"
    assert env.backend.finalized == []
    assert "finalize" not in env.paths()
    assert Counter(judgment.roles) == Counter({"sentencing": 1})  # 검수 0
    assert env.fake.verdicts[VERDICT_ID].sentence_status == "PENDING"
    ledger = await _ledger(env.engine)
    assert ("sentencing", "COMPLETE") in ledger
    writers = [status for node, status in ledger if node == "writer"]
    assert writers and set(writers) == {"UNKNOWN"}
    assert all(status != "RESERVED" for _, status in ledger)
