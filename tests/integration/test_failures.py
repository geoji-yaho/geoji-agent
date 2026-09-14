"""장애 4종 · 벤더 한쪽 장애 2종 · 예산 초과(08 §3.1·§4.1·§4.2 `test_failures`, OP-01).

실제 Postgres + 가짜 백엔드 + FakeLLM 만 쓴다. 실제 벤더 키·네트워크 없음.
계획서의 9초·15초·12초는 **설정과 가짜 백엔드 인자로 줄인다**. 실제 시간은 리허설(08 §3.5)에서 잰다.

① SENTENCE 처리 중 워커 **프로세스** kill — 느린 FakeLLM 워커(subprocess)가 양형 호출을 시작하면
  죽인다. lease 는 `JOB_LEASE_SECONDS=2`(운영 15). 마감 후 분기는 마감을 기다리지 않고
  과거로 당긴다. 마감 전: reaper 재claim · 새 generation. 마감 후: watchdog
  FINAL(RULE)+TEMPLATE_READY · job CANCELLED. 어느 쪽이든 형량 FINAL 1회, 이전 generation 의
  finalize 는 409
② 템플릿 이후 늦은 성공 — 워커가 검수에 들어가기 직전 마감이 지나 watchdog 이 먼저 템플릿을
  확정한다(12초 지연 대신 마감을 당긴다). 늦은 finalize → 409 `STALE_GENERATION` → 폐기,
  모델 재호출 0
③ TEXT_RETRY 중 삭제 — 검수 직전 백엔드가 방 epoch +1 · 무효화 SQL. finalize 0 ·
  `EVIDENCE_INVALIDATED`, 무효화 뒤 `node_results` 재사용 0
④ commit 후 응답 끊김 — 가짜 백엔드가 commit 한 뒤 연결이 끊긴다(httpx transport 가 응답 대신
  `RemoteProtocolError`). 워커가 같은 본문을 재전송 → commit record 로 200 · 같은 `text_version` ·
  `llm_calls` 불변
⑤ xAI 키 무효 흉내(서기 벤더가 `AUTH` 오류) → 형량 AI · 문구 TEMPLATE · `VENDOR_UNAVAILABLE`
⑥ OpenAI 키 무효 흉내 → 양형 RULE · 검수 불가 TEMPLATE · `VENDOR_UNAVAILABLE`
⑦ 예산 초과 `WRITER_NODE_TIMEOUT_SECONDS=0.1` → TEMPLATE / `DEADLINE_EXCEEDED`,
  응답 ≤ deadline + 0.5s

마지막 표 동시 도착은 백엔드 테스트다(10 §13). 여기 두지 않는다.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from geoji_ai.adapters.backend_http import BackendHttp, BackendRejected
from geoji_ai.adapters.fake_llm import FakeCall, FakeLLM, FakeScenario
from geoji_ai.adapters.llm_router import RoleRoutedLLM, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import PostgresJobs, make_engine, reap
from geoji_ai.adapters.postgres_memory import invalidate_scope
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.core.config import Settings
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.ports.llm import LLMError, LLMErrorKind, LLMResult
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration import test_graph_c_flow as _flow
from tests.integration._worker_proc import (
    _free_port,
    _kill,
    _serve,
    _start_fake_llm_worker,
    _wait_for,
)
from tests.integration.test_graph_b import CONTEXT_OK
from tests.integration.test_graph_c_flow import (
    POST_ID,
    ROOM_B,
    VERDICT_ID,
    Env,
    PlannedLLM,
    SpyBackend,
    _rows,
    _snapshot_data,
)
from tests.integration.test_llm_wiring import Capture, roles
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings
from tests.unit.test_backend_http import finalize_request

# `test_graph_c_flow` 의 pytest fixture 를 이 모듈에 등록한다(conftest 는 소유 밖).
env = _flow.env
snapshot_path = _flow.snapshot_path

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

#: ① 첫 워커의 모델 지연. 양형 호출을 시작한 뒤 kill 할 때까지 끝나지 않을 만큼 길면 된다.
SLOW_LLM_MS = 30_000
#: ① 판결 마감. 두 워커 기동과 lease 만료를 합쳐도 마감 전 분기가 마감을 넘지 않는 값.
KILL_CASE_DEADLINE_S = 90
#: ⑦ 계획서 값(08 §3.1·§3.5).
WRITER_TIMEOUT_OVER_BUDGET_S = 0.1
#: ⑦ 서기 가짜 지연. 서기 상한(0.1s)보다 길다.
WRITER_SLOW_MS = 1_000
#: ⑦ "응답 ≤ deadline + 0.5s"(08 §3.5).
RESPONSE_SLACK_S = 0.5


# --- 준비물 ----------------------------------------------------------------------


class VendorDown(FakeLLM):
    """키 무효 흉내. 모든 호출이 벤더 오류 `kind` 로 끝난다(06 §4.2 리허설의 가짜 판)."""

    def __init__(self, kind: LLMErrorKind = "AUTH") -> None:
        super().__init__()
        self.kind: LLMErrorKind = kind

    async def structured_call(
        self,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        self.calls.append(
            FakeCall(role, schema, messages, timeout_s, max_output_tokens, self.scenario)
        )
        raise LLMError(self.kind, message="fake: invalid api key")


class CountingLedger(PostgresCallLedger):
    """`node_results` 재사용 hit 를 센다."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self.hits: list[str] = []

    async def get_node_result(self, request_hash: str, versions: dict[str, Any]) -> Any:
        found = await super().get_node_result(request_hash, versions)
        if found is not None:
            self.hits.append(request_hash)
        return found


def _gateway(
    engine: AsyncEngine,
    settings: Settings,
    *,
    judgment: Any,
    writer: Any,
    ledger: PostgresCallLedger | None = None,
) -> LLMGateway:
    """운영 조립(`run_worker`)과 같은 모양. 벤더 어댑터 자리에 가짜를 꽂는다."""
    router = RoleRoutedLLM(
        {"openai": judgment, "xai": writer},
        models={"openai": settings.MODEL_JUDGMENT, "xai": settings.MODEL_WRITER},
    )
    return LLMGateway(
        router,
        ledger or PostgresCallLedger(engine),
        VendorHealth(),
        price_for,
        stale_scopes=PostgresPreparation(engine).stale_scopes,
    )


async def _no_sleep(_: float) -> None:
    return None


async def _slots(engine: AsyncEngine) -> list[tuple[str, int, str]]:
    rows = await _rows(
        engine, "SELECT node, call_index, status FROM ai.llm_calls ORDER BY node, call_index"
    )
    return [(row["node"], row["call_index"], row["status"]) for row in rows]


async def _retain_count(engine: AsyncEngine) -> int:
    return len(await _rows(engine, "SELECT id FROM ai.jobs WHERE kind = 'RETAIN'"))


async def _view(app: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake-backend"
    ) as client:
        response = await client.get(f"/posts/{POST_ID}/verdict")
    assert response.status_code == 200
    return response.json()


def _errors(state: dict[str, Any], role: str) -> set[str | None]:
    return {record.error for record in state["calls"] if record.role == role}


def _round_one() -> list[dict[str, Any]]:
    # 10 §7 round 1 은 템플릿 후 5분.
    return [{"verdict_id": VERDICT_ID, "verdict_version": 1, "round": 1, "delay_s": 300}]


def _log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


async def _served_env(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    app: Any,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Env:
    client = httpx.AsyncClient(transport=transport or httpx.ASGITransport(app=app))
    http = BackendHttp("http://fake-backend", FAKE_SERVICE_TOKEN, client=client, sleep=_no_sleep)
    return Env(engine, jobs, enqueue, fetch_job, app, SpyBackend(http))


async def _seed_snapshot_epochs(engine: AsyncEngine) -> None:
    snapshot = CaseSnapshot.model_validate(_snapshot_data())
    await seed_epochs(engine, {pv.scope_key: pv.epoch for pv in snapshot.privacy_versions})


# --- ① SENTENCE 처리 중 워커 kill --------------------------------------------------


@pytest.fixture
async def kill_env(
    test_database_url: str,
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    snapshot_path: Path,
) -> AsyncIterator[Env]:
    """uvicorn 스레드에서 도는 가짜 백엔드는 NullPool 엔진을 쓴다(이벤트 루프가 다르다)."""
    await _seed_snapshot_epochs(engine)
    fake_engine = make_engine(test_database_url, poolclass=NullPool)
    app = create_fake_backend(jobs_engine=fake_engine, snapshot_fixture=snapshot_path)
    served = await _served_env(engine, jobs, enqueue, fetch_job, app)
    try:
        yield served
    finally:
        await served.backend.inner.aclose()
        await fake_engine.dispose()


@pytest.mark.parametrize("branch", ["마감_전", "마감_후"])
async def test_01_SENTENCE_처리_중_워커_kill_형량_FINAL_1회_이전_generation_finalize_409(
    branch: str,
    kill_env: Env,
    test_database_url: str,
    tmp_path: Path,
):
    env = kill_env
    fake = env.fake
    # 준비 그래프는 이 프로세스에서 끝낸다. 워커는 PREP 경로로 바로 양형을 부른다.
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    deadline_at = datetime.now(UTC) + timedelta(seconds=KILL_CASE_DEADLINE_S)
    fake.seed_verdict(VERDICT_ID, verdict_version=1, post_id=POST_ID, deadline_at=deadline_at)
    job_id = await env.enqueue(
        "SENTENCE",
        verdict_id=VERDICT_ID,
        verdict_version=1,
        post_id=POST_ID,
        deadline_at=deadline_at,
    )
    port = _free_port()
    backend_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "worker.log"

    with _serve(env.app, port):
        # 1) 느린 모델 워커가 양형 호출(원장 예약)을 시작하면 프로세스를 죽인다.
        first = _start_fake_llm_worker(
            test_database_url, backend_url, log_path, latency_ms=SLOW_LLM_MS
        )
        try:

            async def in_sentencing() -> bool:
                if first.poll() is not None:
                    return True
                rows = await _rows(
                    env.engine, "SELECT id FROM ai.llm_calls WHERE node = 'sentencing'"
                )
                return bool(rows)

            await _wait_for(in_sentencing, "양형 호출 시작")
            assert first.poll() is None, _log(log_path)
            row = await env.fetch_job(job_id)
            assert row["status"] == "RUNNING"
            old_generation = str(row["generation_id"])
            assert fake.verdicts[VERDICT_ID].active_generation_id == old_generation
        finally:
            _kill(first)
        assert fake.sentence_fixes == []

        if branch == "마감_전":
            # 2) lease 만료 → reaper 가 QUEUED 로 되살린다 → 새 워커가 새 generation 으로 끝낸다.
            async def reaped() -> bool:
                return await reap(env.engine) > 0

            await _wait_for(reaped, "lease 회수")
            row = await env.fetch_job(job_id)
            assert (row["status"], row["last_error_code"]) == ("QUEUED", "LEASE_EXPIRED")

            second = _start_fake_llm_worker(test_database_url, backend_url, log_path, latency_ms=0)
            try:

                async def finished() -> bool:
                    status = (await env.fetch_job(job_id))["status"]
                    return status in {"SUCCEEDED", "FAILED", "CANCELLED"}

                await _wait_for(finished, "새 generation 종료")
            finally:
                _kill(second)

            row = await env.fetch_job(job_id)
            assert row["status"] == "SUCCEEDED", _log(log_path)
            assert str(row["generation_id"]) != old_generation
            assert row["attempts"] == 2
            verdict = fake.verdicts[VERDICT_ID]
            assert (verdict.sentence_status, verdict.sentence_source) == ("FINAL", "AI")
            assert fake.sentence_fixes == [(VERDICT_ID, "AI")]
            # 이전 generation 으로 같은 finalize 를 보내면 409
            late = FinalizeRequest.model_validate(
                {**fake.finalize_bodies[-1], "generation_id": old_generation}
            )
        else:
            # 2) 마감이 지났다. 기다리지 않고 마감을 과거로 당긴다(판결·job 둘 다).
            fake.verdicts[VERDICT_ID].deadline_at = datetime.now(UTC) - timedelta(milliseconds=1)
            async with env.engine.begin() as conn:
                await conn.execute(
                    text("UPDATE ai.jobs SET deadline_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": job_id},
                )
            assert await fake.run_watchdog() == [VERDICT_ID]
            row = await env.fetch_job(job_id)
            assert row["status"] == "CANCELLED"
            assert await reap(env.engine) == 0  # 이미 끝난 job 은 되살리지 않는다
            verdict = fake.verdicts[VERDICT_ID]
            assert (verdict.sentence_status, verdict.sentence_source, verdict.text_status) == (
                "FINAL",
                "RULE",
                "TEMPLATE_READY",
            )
            assert fake.text_retry_rounds == _round_one()
            assert await _retain_count(env.engine) == 1
            # watchdog 이 다시 돌아도 반복하지 않는다
            assert await fake.run_watchdog() == []
            assert fake.sentence_fixes == [(VERDICT_ID, "RULE")]
            late = finalize_request(job_id=job_id, generation_id=old_generation)

        backend = BackendHttp(backend_url, FAKE_SERVICE_TOKEN)
        try:
            with pytest.raises(BackendRejected) as caught:
                await backend.finalize(VERDICT_ID, late)
        finally:
            await backend.aclose()
        assert (caught.value.status, caught.value.code) == (409, "STALE_GENERATION")
        assert len(fake.sentence_fixes) == 1


# --- ② 템플릿 이후 늦은 성공 ----------------------------------------------------------


async def test_02_템플릿_이후_늦은_성공은_409_STALE_GENERATION_으로_폐기되고_템플릿이_남는다(
    env: Env,
):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    watchdog: dict[str, Any] = {}

    async def late(role: str) -> None:
        if role == "evaluator" and not watchdog:
            env.fake.verdicts[VERDICT_ID].deadline_at = datetime.now(UTC) - timedelta(
                milliseconds=1
            )
            watchdog["fixed"] = await env.fake.run_watchdog()
            watchdog["roles"] = Counter(call[0] for call in llm.calls)

    llm = PlannedLLM(before=late)
    rejected: list[tuple[int, str | None]] = []
    spy_finalize = env.backend.finalize

    async def recording_finalize(verdict_id: str, req: FinalizeRequest) -> Any:
        try:
            return await spy_finalize(verdict_id, req)
        except BackendRejected as exc:
            rejected.append((exc.status, exc.code))
            raise

    env.backend.finalize = recording_finalize  # type: ignore[method-assign]

    job_id = await env.sentence(llm)

    assert watchdog["fixed"] == [VERDICT_ID]
    assert rejected == [(409, "STALE_GENERATION")]
    # 늦은 finalize 1회 → 409 → 폐기. repair·재호출 없음(08 §4.1 모델 재호출 0).
    assert env.paths().count("finalize") == 1
    assert len(env.backend.finalized) == 1
    assert env.backend.failed == []
    assert (
        llm.roles() == watchdog["roles"] == Counter({"sentencing": 1, "writer": 2, "evaluator": 1})
    )
    assert env.fake.commit_records == {}
    # 화면은 템플릿 유지, round 1 은 5분 뒤
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source, verdict.text_status) == (
        "FINAL",
        "RULE",
        "TEMPLATE_READY",
    )
    assert verdict.text_version == 0
    assert env.fake.sentence_fixes == [(VERDICT_ID, "RULE")]
    assert env.fake.text_retry_rounds == _round_one()
    assert (await _view(env.app))["view"] == {"source": "TEMPLATE"}
    assert await _retain_count(env.engine) == 1
    # watchdog 이 job 을 CANCELLED 로 끝냈다(10 §6 4단계). 워커의 complete 는 0행이다.
    row = await env.fetch_job(job_id)
    assert (row["status"], row["last_error_code"]) == ("CANCELLED", None)


# --- ③ TEXT_RETRY 중 삭제 -------------------------------------------------------------


async def _node_results(engine: AsyncEngine) -> list[dict[str, Any]]:
    return await _rows(
        engine,
        "SELECT CAST(call_id AS text) AS call_id, request_hash, model_id, prompt_version, "
        "policy_version, privacy_versions, invalidated_at FROM ai.node_results",
    )


async def _reusable(ledger: PostgresCallLedger, row: dict[str, Any]) -> bool:
    versions = {
        key: row[key]
        for key in ("model_id", "prompt_version", "policy_version", "privacy_versions")
    }
    return await ledger.get_node_result(row["request_hash"], versions) is not None


async def _retry_during_deletion(env: Env) -> dict[str, Any]:
    """PREPARE(원장) → SENTENCE(hell 서기 실패) → TEXT_RETRY 검수 직전 방 B 삭제.

    관찰값(원장·삭제 전 node_results call_id·text 상태)을 돌려준다.
    """
    settings = make_settings()
    ledger = CountingLedger(env.engine)
    # 테스트가 직접 조회할 때 쓰는 원장. 워커 경로의 hit 기록(`ledger.hits`)을 섞지 않는다.
    probe = PostgresCallLedger(env.engine)
    prep_gateway = _gateway(
        env.engine,
        settings,
        judgment=FakeLLM(outputs={"context": CONTEXT_OK}),
        writer=FakeLLM(),
        ledger=ledger,
    )
    await env.prepare(prep_gateway)
    await env.sentence(PlannedLLM(FakeLLM(FakeScenario.INTENSITY_FAIL)))
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source, verdict.text_status) == (
        "FINAL",
        "AI",
        "TEMPLATE_READY",
    )
    before = (verdict.text_version, dict(verdict.text_sources))
    assert await _node_results(env.engine)
    env.backend.finalized.clear()
    env.backend.failed.clear()
    env.fake.calls.clear()

    deleted: dict[str, Any] = {}

    async def delete_room(role: str) -> None:
        if role == "evaluator" and not deleted:
            rows = await _node_results(env.engine)
            assert all([await _reusable(probe, row) for row in rows])
            deleted["call_ids"] = {row["call_id"] for row in rows}
            # 백엔드 삭제 트랜잭션: epoch 먼저 올리고 무효화(10 §8)
            await seed_epochs(env.engine, {f"room:{ROOM_B}": 2})
            async with env.engine.begin() as conn:
                await invalidate_scope(
                    conn, source_type="ROOM", source_id=ROOM_B, scope_key=f"room:{ROOM_B}"
                )

    retry_gateway = _gateway(
        env.engine,
        settings,
        judgment=PlannedLLM(FakeLLM(), before=delete_room),
        writer=FakeLLM(),
        ledger=ledger,
    )
    ledger.hits.clear()

    retry_id = await env.sentence(retry_gateway, kind="TEXT_RETRY", settings=settings)

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert deleted["call_ids"]
    return {
        "settings": settings,
        "ledger": ledger,
        "probe": probe,
        "before": before,
        "deleted_call_ids": deleted["call_ids"],
        "verdict": verdict,
    }


async def _next_round(env: Env, seen: dict[str, Any]) -> None:
    """무효화 뒤 다음 TEXT_RETRY round(round 2). 기록을 비우고 원장 게이트웨이로 돈다."""
    seen["ledger"].hits.clear()
    env.backend.finalized.clear()
    env.backend.failed.clear()
    env.fake.calls.clear()
    settings = seen["settings"]
    gateway = _gateway(
        env.engine, settings, judgment=FakeLLM(), writer=FakeLLM(), ledger=seen["ledger"]
    )
    await env.enqueue("TEXT_RETRY", verdict_id=VERDICT_ID, verdict_version=1, round=2)
    job = await env.jobs.claim(["TEXT_RETRY"], _flow.WORKER_ID)
    assert job is not None
    await SentenceHandler()(job, env.ctx(job, gateway, settings))


async def test_03a_TEXT_RETRY_중_삭제면_finalize_0_EVIDENCE_INVALIDATED_node_results_재사용_0(
    env: Env, snapshot_path: Path
):
    seen = await _retry_during_deletion(env)
    verdict, before, probe = seen["verdict"], seen["before"], seen["probe"]

    assert "finalize" not in env.paths()
    assert env.backend.finalized == []
    assert env.backend.failed == ["EVIDENCE_INVALIDATED"]
    assert (verdict.text_version, verdict.text_sources) == before
    assert verdict.text_status == "TEMPLATE_READY"
    assert (await _view(env.app))["view"] == {"source": "TEMPLATE"}
    # 삭제 전에 저장된 node_results 는 전부 무효 → 같은 키로 조회해도 없다
    stale = [
        row for row in await _node_results(env.engine) if row["call_id"] in seen["deleted_call_ids"]
    ]
    assert stale and all(row["invalidated_at"] is not None for row in stale)
    assert not any([await _reusable(probe, row) for row in stale])
    assert seen["ledger"].hits == []

    # 다음 round: 백엔드 스냅샷은 삭제 뒤 epoch 를 싣는다(10 §8). 재사용 0 · 저장 0
    data = _snapshot_data()
    for pv in data["privacy_versions"]:
        if pv["scope_key"] == f"room:{ROOM_B}":
            pv["epoch"] = 2
    snapshot_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    await _next_round(env, seen)

    assert seen["ledger"].hits == []
    assert "finalize" not in env.paths()
    assert env.backend.finalized == []
    assert (verdict.text_version, verdict.text_sources) == before


async def test_03b_무효화_뒤에_끝난_호출은_옛_epoch_로_node_results_에_남지_않는다(env: Env):
    await _retry_during_deletion(env)
    old_scope = {"scope_key": f"room:{ROOM_B}", "epoch": 1}

    live_old_epoch = [
        row["call_id"]
        for row in await _node_results(env.engine)
        if row["invalidated_at"] is None and old_scope in row["privacy_versions"]
    ]
    assert live_old_epoch == []


async def test_03c_무효화_뒤_옛_epoch_스냅샷의_다음_round_는_node_results_를_재사용하지_않는다(
    env: Env,
):
    seen = await _retry_during_deletion(env)
    # 스냅샷은 삭제 전 epoch 그대로 둔다(늦게 도착한 요청)
    await _next_round(env, seen)

    assert seen["ledger"].hits == []


# --- ④ commit 후 응답 끊김 ------------------------------------------------------------


class DropAfterCommit(httpx.AsyncBaseTransport):
    """finalize 를 가짜 백엔드까지 보내 commit 시킨 뒤, 첫 `drops` 번은 응답 대신 연결을 끊는다."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        on_finalize: Callable[[], Awaitable[int]],
        *,
        drops: int = 1,
    ) -> None:
        self.inner = inner
        self.on_finalize = on_finalize
        self.drops = drops
        self.bodies: list[bytes] = []
        self.responses: list[tuple[int, dict[str, Any]]] = []
        self.llm_call_counts: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if not request.url.path.endswith("/finalize"):
            return response
        body = await response.aread()
        self.bodies.append(request.content)
        self.responses.append((response.status_code, json.loads(body)))
        self.llm_call_counts.append(await self.on_finalize())
        if self.drops:
            self.drops -= 1
            raise httpx.RemoteProtocolError(
                "fake: commit 뒤 응답 전에 연결이 끊겼다", request=request
            )
        return httpx.Response(
            response.status_code, headers=response.headers, content=body, request=request
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


async def test_04_commit_뒤_응답이_끊기면_같은_본문_재전송이_200_같은_text_version_모델_재호출_0(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    snapshot_path: Path,
):
    await _seed_snapshot_epochs(engine)
    app = create_fake_backend(jobs_engine=engine, snapshot_fixture=snapshot_path)

    async def llm_calls() -> int:
        return len(await _rows(engine, "SELECT id FROM ai.llm_calls"))

    transport = DropAfterCommit(httpx.ASGITransport(app=app), llm_calls)
    served = await _served_env(engine, jobs, enqueue, fetch_job, app, transport)
    try:
        await served.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
        settings = make_settings()
        judgment, writer = FakeLLM(), FakeLLM()
        gateway = _gateway(engine, settings, judgment=judgment, writer=writer)

        job_id = await served.sentence(gateway, settings=settings)
    finally:
        await served.backend.inner.aclose()

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert served.backend.failed == []
    # 그래프는 finalize 를 한 번 불렀고, 어댑터가 같은 본문을 한 번 더 보냈다
    assert len(served.backend.finalized) == 1
    assert served.paths().count("finalize") == 2
    assert len(transport.bodies) == 2 and transport.bodies[0] == transport.bodies[1]
    (first_status, first), (second_status, second) = transport.responses
    assert first_status == second_status == 200
    assert first == second and second["text_version"] == 1
    fake = served.fake
    assert len(fake.commit_records) == 1
    assert fake.verdicts[VERDICT_ID].text_version == 1
    assert fake.sentence_fixes == [(VERDICT_ID, "AI")]
    assert await _retain_count(engine) == 1
    # 모델 재호출 0: finalize 두 번 사이 원장 행 불변, 호출 수는 보정 없는 유죄 2강도 그대로
    assert transport.llm_call_counts[0] == transport.llm_call_counts[1] == await llm_calls()
    assert roles(judgment) == Counter({"sentencing": 1, "evaluator": 1})
    assert roles(writer) == Counter({"writer": 2})


# --- ⑤ xAI 키 무효 -------------------------------------------------------------------


async def test_05_xAI_키_무효면_형량은_AI_문구는_TEMPLATE_VENDOR_UNAVAILABLE(env: Env):
    """현행 동작 고정: 서기 전부 실패 → generation_failed(VENDOR_UNAVAILABLE).

    저장 형량은 백엔드 폴백 RULE. 사용자 결정 대기
    (06 §3.1·08 §3.5 장애 1 "형량 AI 정상" vs 10 §4.6 폴백). 결정되면 기대를 바꾼다.
    """
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings()
    judgment, writer = FakeLLM(), VendorDown("AUTH")
    capture = Capture()

    job_id = await env.sentence(
        _gateway(env.engine, settings, judgment=judgment, writer=writer),
        settings=settings,
        handler=SentenceHandler(capture),
    )

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert roles(judgment) == Counter({"sentencing": 1})
    assert roles(writer) == Counter({"writer": 2})
    assert await _slots(env.engine) == [
        ("sentencing", 0, "COMPLETE"),
        ("writer", 0, "FAILED"),
        ("writer", 1, "FAILED"),
    ]
    state = capture.last
    assert state["sentencing_source"] == "AI"
    assert _errors(state, "writer") == {"AUTH"}
    assert state["draft_sources"] == {Intensity.spicy: "TEMPLATE", Intensity.hell: "TEMPLATE"}
    assert env.backend.finalized == []
    assert env.backend.failed == ["VENDOR_UNAVAILABLE"]
    assert env.fake.text_retry_rounds == _round_one()
    # 저장은 generation-failed 폴백이다(10 §4.6): 백엔드가 FINAL/RULE + 템플릿으로 확정한다.
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source, verdict.text_status) == (
        "FINAL",
        "RULE",
        "TEMPLATE_READY",
    )


# --- ⑥ OpenAI 키 무효 ----------------------------------------------------------------


async def test_06_OpenAI_키_무효면_양형_RULE_검수_불가_TEMPLATE_VENDOR_UNAVAILABLE(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings()
    judgment, writer = VendorDown("AUTH"), FakeLLM()
    capture = Capture()

    job_id = await env.sentence(
        _gateway(env.engine, settings, judgment=judgment, writer=writer),
        settings=settings,
        handler=SentenceHandler(capture),
    )

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert roles(writer) == Counter({"writer": 2})
    state = capture.last
    assert state["sentencing_source"] == "RULE"
    assert _errors(state, "sentencing") == {"AUTH"}
    assert _errors(state, "evaluator") == {"AUTH"}
    assert set(state["draft_sources"].values()) == {"TEMPLATE"}
    assert env.backend.finalized == []
    assert env.backend.failed == ["VENDOR_UNAVAILABLE"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source, verdict.text_status) == (
        "FINAL",
        "RULE",
        "TEMPLATE_READY",
    )


# --- ⑦ 예산 초과 ----------------------------------------------------------------------


def test_07a_WRITER_NODE_TIMEOUT_SECONDS_0_1_을_설정으로_줄_수_있다():
    settings = Settings(_env_file=None, WRITER_NODE_TIMEOUT_SECONDS=WRITER_TIMEOUT_OVER_BUDGET_S)
    assert settings.WRITER_NODE_TIMEOUT_SECONDS == WRITER_TIMEOUT_OVER_BUDGET_S


def _over_budget_settings() -> Settings:
    return make_settings(WRITER_NODE_TIMEOUT_SECONDS=WRITER_TIMEOUT_OVER_BUDGET_S)


async def _run_over_budget(env: Env) -> tuple[dict[str, Any], datetime, datetime, float]:
    """(state, 끝난 시각, 판결 마감, SENTENCE 경과 초)."""
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = _over_budget_settings()
    capture = Capture()
    gateway = _gateway(
        env.engine, settings, judgment=FakeLLM(), writer=FakeLLM(latency_ms=WRITER_SLOW_MS)
    )
    started = time.monotonic()
    await env.sentence(
        gateway,
        settings=settings,
        handler=SentenceHandler(capture),
        remaining_s=float(settings.FIRST_RESULT_TARGET_SECONDS),
    )
    elapsed = time.monotonic() - started
    finished = datetime.now(UTC)
    deadline = env.fake.verdicts[VERDICT_ID].deadline_at
    assert deadline is not None
    return capture.last, finished, deadline, elapsed


async def test_07b_서기_예산_초과면_TEMPLATE_응답은_deadline_0_5s_안(env: Env):
    state, finished, deadline, elapsed = await _run_over_budget(env)

    assert state["sentencing_source"] == "AI"
    # 상한 0.1s 가 실제로 걸렸다: 서기는 TIMEOUT, 서기 가짜 지연(1s)을 기다리지 않았다
    assert _errors(state, "writer") == {"TIMEOUT"}
    assert elapsed < WRITER_SLOW_MS / 1000
    assert set(state["draft_sources"].values()) == {"TEMPLATE"}
    assert env.backend.finalized == []
    assert len(env.backend.failed) == 1
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.text_status) == ("FINAL", "TEMPLATE_READY")
    assert finished <= deadline + timedelta(seconds=RESPONSE_SLACK_S)


async def test_07c_서기_예산_초과_코드는_DEADLINE_EXCEEDED(env: Env):
    await _run_over_budget(env)

    assert env.backend.failed == ["DEADLINE_EXCEEDED"]
