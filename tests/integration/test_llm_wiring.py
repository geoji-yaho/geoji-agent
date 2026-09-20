"""원장·벤더 장애·node_results 그래프 배선(06 §3.1·§3.2·§4.2, 스펙 케이스 ⑨~⑫·⑬·⑭).

실제 Postgres(`PostgresCallLedger`) + `LLMGateway` + `RoleRoutedLLM`(모델별 FakeLLM) + 가짜 백엔드 +
dispatch 핸들러. 원장 모델 id 는 라우팅한 설정 모델(단가표에 있음)로 남는다. 네트워크·키 없음.

⑨ 유죄·2강도 SENTENCE → `llm_calls` 양형 1·서기 2(call_index 0,1)·검수 1, `reserved = 0`
⑩ cap 을 낮춰 서기 예산 초과 → 서기 TEMPLATE·`BUDGET_EXCEEDED`, 형량은 AI 그대로
⑪ xAI degraded → 서기 호출 0·TEMPLATE, 양형 AI
⑫ OpenAI degraded → 양형 RULE·검수 불가 → `VENDOR_UNAVAILABLE`
⑬ 같은 입력 PREPARE 재실행 → 조서 재사용(inner 0), prompt_version 만 바꾸면 재호출
⑭ hell 별도 검수가 `MODEL_EVALUATOR_HELL` 모델로 원장에 남음
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.llm_router import RoleRoutedLLM, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.llm_gateway import LLMGateway
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.core.config import Settings
from geoji_ai.domain.budget import CASE_CAP_MICRO_USD
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.vendor_health import DEGRADED_THRESHOLD, VendorHealth
from geoji_ai.graphs.preparation import PrepareDeps, run_preparation
from geoji_ai.graphs.sentencing import build_sentence_graph
from geoji_ai.prompts import prompt_bundle_version
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration.test_graph_b import CONTEXT_OK
from tests.integration.test_graph_c_flow import (
    POST_ID,
    WORKER_ID,
    Env,
    PlannedLLM,
    SpyBackend,
    _rows,
    _snapshot_data,
)
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

#: ⑩ 의 낮춘 cap. 양형 예약(출력 400 × luna 1.2 + 입력)은 들어가고, 서기 예약은 출력만으로
#: 700 × grok 2.5 = 1,750 이라 들어가지 않는다.
LOW_CAP_MICRO_USD = 1_500
HELL_MODEL = "gpt-5.6-terra"

#: ⑭ 의 cap. hell 검수 예약(출력 3000 × terra 12.0 = 36,000 + 입력)만으로 넘게 만드는 값이고
#: 제품 상한이 아니다. 예전에는 제품 상한(40원 = 27,586)이 마침 이 자리였는데 100원으로
#: 올리면서 경계가 사라졌다(9/20). 경계를 테스트에 박아 둔다.
HELL_EVAL_CAP_MICRO_USD = 27_586


# --- 준비물 ----------------------------------------------------------------------


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "case-snapshot-llm-wiring.json"
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


@dataclass
class Wired:
    gateway: LLMGateway
    judgment: Any
    writer: FakeLLM
    hell: FakeLLM | None


def wire(
    engine: AsyncEngine,
    settings: Settings,
    *,
    cap: int = CASE_CAP_MICRO_USD,
    health: VendorHealth | None = None,
    hell: bool = False,
    judgment: Any | None = None,
) -> Wired:
    """운영 조립(`run_worker`)과 같은 모양. 벤더 어댑터 자리에 모델별 FakeLLM 을 꽂는다."""
    judgment = judgment or FakeLLM(outputs={"context": CONTEXT_OK})
    writer = FakeLLM()
    hell_llm = FakeLLM() if hell else None
    router = RoleRoutedLLM(
        {"openai": judgment, "xai": writer},
        models={"openai": settings.MODEL_JUDGMENT, "xai": settings.MODEL_WRITER},
        model_adapters={settings.MODEL_EVALUATOR_HELL: hell_llm} if hell_llm else None,
    )
    ledger = PostgresCallLedger(engine, cap_micro_usd=cap)
    gateway = LLMGateway(
        router,
        ledger,
        health or VendorHealth(),
        price_for,
        stale_scopes=PostgresPreparation(engine).stale_scopes,
    )
    return Wired(gateway, judgment, writer, hell_llm)


def degraded(vendor: str) -> VendorHealth:
    health = VendorHealth()
    for _ in range(DEGRADED_THRESHOLD):
        health.record_failure(vendor, "SERVER")
    assert health.is_degraded(vendor)
    return health


def roles(llm: FakeLLM | None) -> Counter[str]:
    return Counter() if llm is None else Counter(call.role for call in llm.calls)


class Capture:
    """그래프 C 의 마지막 state 를 잡는 `graph_factory`."""

    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []

    def __call__(self, deps: Any) -> Any:
        graph = build_sentence_graph(deps)

        async def ainvoke(state: Any) -> Any:
            final = await graph.ainvoke(state)
            self.states.append(final)
            return final

        return SimpleNamespace(ainvoke=ainvoke)

    @property
    def last(self) -> dict[str, Any]:
        return self.states[-1]


async def _calls(engine: AsyncEngine) -> list[dict[str, Any]]:
    return await _rows(
        engine,
        "SELECT node, call_index, status, vendor, model_id, "
        "CAST(job_id AS text) AS job_id, CAST(generation_id AS text) AS generation_id, "
        "estimated_max_micro_usd FROM ai.llm_calls ORDER BY node, call_index, started_at",
    )


def _slots(rows: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    return [(row["node"], row["call_index"], row["status"]) for row in rows]


async def _budget(engine: AsyncEngine) -> dict[str, Any]:
    rows = await _rows(
        engine,
        "SELECT post_id, cap_micro_usd, spent_micro_usd, reserved_micro_usd FROM ai.case_budgets",
    )
    assert len(rows) == 1
    return rows[0]


def _errors(state: dict[str, Any], role: str) -> list[str | None]:
    return [record.error for record in state["calls"] if record.role == role]


# --- ⑨ 원장 행 ------------------------------------------------------------------------


async def test_09_유죄_2강도_SENTENCE_원장은_양형1_서기2_검수1_reserved_0(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))  # 원장 없는 경로로 prep
    settings = make_settings()
    wired = wire(env.engine, settings)

    job_id = await env.sentence(wired.gateway, settings=settings)

    job = await env.fetch_job(job_id)
    assert job["status"] == "SUCCEEDED"
    assert len(env.backend.finalized) == 1
    assert env.backend.failed == []

    rows = await _calls(env.engine)
    assert _slots(rows) == [
        ("evaluator", 0, "COMPLETE"),
        ("sentencing", 0, "COMPLETE"),
        ("writer", 0, "COMPLETE"),
        ("writer", 1, "COMPLETE"),
    ]
    models = {(row["node"], row["call_index"]): (row["vendor"], row["model_id"]) for row in rows}
    assert models[("sentencing", 0)] == ("openai", settings.MODEL_JUDGMENT)
    assert models[("evaluator", 0)] == ("openai", settings.MODEL_JUDGMENT)
    assert models[("writer", 0)] == ("xai", settings.MODEL_WRITER)
    assert {row["job_id"] for row in rows} == {job_id}
    assert {row["generation_id"] for row in rows} == {str(job["generation_id"])}

    budget = await _budget(env.engine)
    assert budget["post_id"] == POST_ID
    assert budget["reserved_micro_usd"] == 0
    # FakeLLM 비용은 모름 → est_max 를 spent 에 더한다(원장 규칙)
    assert budget["spent_micro_usd"] == sum(row["estimated_max_micro_usd"] for row in rows)

    assert roles(wired.judgment) == Counter({"sentencing": 1, "evaluator": 1})
    assert roles(wired.writer) == Counter({"writer": 2})


async def test_09b_남은_10s_유죄_SENTENCE_에서_양형_AI_가_원장에_남는다(env: Env):
    """R1: 10 §3 기본 마감(confirmed_at + 10s)에서도 양형관을 부른다(예약 0, 05 §3.1)."""
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings()
    wired = wire(env.engine, settings)
    capture = Capture()

    job_id = await env.sentence(
        wired.gateway,
        settings=settings,
        remaining_s=10.0,
        handler=SentenceHandler(capture),
    )

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert capture.last["sentencing_source"] == "AI"
    rows = await _calls(env.engine)
    assert ("sentencing", 0, "COMPLETE") in _slots(rows)
    assert roles(wired.judgment)["sentencing"] == 1
    assert (await _budget(env.engine))["reserved_micro_usd"] == 0


# --- ⑩ 예산 초과 ----------------------------------------------------------------------


async def test_10_cap_을_낮추면_서기_예산_초과로_TEMPLATE_형량은_AI_그대로(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    # 낮은 cap에서 양형은 허용하고 서기만 차단하는 경계를 고정한다(제품 상한 아님).
    settings = make_settings(SENTENCING_MAX_OUTPUT_TOKENS=400)
    wired = wire(env.engine, settings, cap=LOW_CAP_MICRO_USD)
    capture = Capture()

    job_id = await env.sentence(wired.gateway, settings=settings, handler=SentenceHandler(capture))

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert _slots(await _calls(env.engine)) == [("sentencing", 0, "COMPLETE")]
    assert roles(wired.writer) == Counter()
    state = capture.last
    assert state["sentencing_source"] == "AI"
    assert _errors(state, "writer") == ["BUDGET", "BUDGET"]
    assert state["draft_sources"] == {Intensity.spicy: "TEMPLATE", Intensity.hell: "TEMPLATE"}
    assert env.backend.finalized == []
    assert env.backend.failed == ["BUDGET_EXCEEDED"]
    budget = await _budget(env.engine)
    assert budget["cap_micro_usd"] == LOW_CAP_MICRO_USD
    assert budget["reserved_micro_usd"] == 0
    assert budget["spent_micro_usd"] <= LOW_CAP_MICRO_USD


# --- ⑪ xAI degraded --------------------------------------------------------------------


async def test_11_xAI_degraded_면_서기_호출_없이_TEMPLATE_양형은_AI(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings()
    wired = wire(env.engine, settings, health=degraded("xai"))
    capture = Capture()

    job_id = await env.sentence(wired.gateway, settings=settings, handler=SentenceHandler(capture))

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert _slots(await _calls(env.engine)) == [("sentencing", 0, "COMPLETE")]
    assert roles(wired.writer) == Counter()
    assert roles(wired.judgment) == Counter({"sentencing": 1})
    state = capture.last
    assert state["sentencing_source"] == "AI"
    assert _errors(state, "writer") == ["DEGRADED", "DEGRADED"]
    assert state["draft_sources"] == {Intensity.spicy: "TEMPLATE", Intensity.hell: "TEMPLATE"}
    assert env.backend.failed == ["VENDOR_UNAVAILABLE"]


# --- ⑫ OpenAI degraded -----------------------------------------------------------------


async def test_12_OpenAI_degraded_면_양형_RULE_검수_불가_VENDOR_UNAVAILABLE(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings()
    wired = wire(env.engine, settings, health=degraded("openai"))
    capture = Capture()

    job_id = await env.sentence(wired.gateway, settings=settings, handler=SentenceHandler(capture))

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert roles(wired.judgment) == Counter()
    assert _slots(await _calls(env.engine)) == [
        ("writer", 0, "COMPLETE"),
        ("writer", 1, "COMPLETE"),
    ]
    state = capture.last
    assert state["sentencing_source"] == "RULE"
    assert _errors(state, "sentencing") == ["DEGRADED"]
    assert _errors(state, "evaluator") == ["DEGRADED"]
    assert set(state["draft_sources"].values()) == {"TEMPLATE"}
    assert env.backend.finalized == []
    assert env.backend.failed == ["VENDOR_UNAVAILABLE"]


# --- ⑬ PREPARE 재실행 재사용 --------------------------------------------------------------


async def _context_rows(engine: AsyncEngine) -> int:
    rows = await _rows(engine, "SELECT id FROM ai.llm_calls WHERE node = 'context'")
    return len(rows)


async def test_13_같은_입력_PREPARE_재실행은_조서를_재사용하고_prompt_version_이_바뀌면_다시_부른다(
    env: Env,
):
    settings = make_settings()
    wired = wire(env.engine, settings)

    await env.prepare(wired.gateway)

    assert roles(wired.judgment)["context"] == 1
    assert await _context_rows(env.engine) == 1
    stored = await _rows(
        env.engine,
        "SELECT n.model_id, n.prompt_version, n.policy_version FROM ai.node_results n "
        "JOIN ai.llm_calls c ON c.id = n.call_id WHERE c.node = 'context'",
    )
    assert stored == [
        {
            "model_id": settings.MODEL_JUDGMENT,
            "prompt_version": prompt_bundle_version(),
            "policy_version": settings.GUARDRAIL_POLICY_VERSION,
        }
    ]
    banter = _slots(
        await _rows(
            env.engine,
            "SELECT node, call_index, status FROM ai.llm_calls "
            "WHERE node = 'banter' ORDER BY call_index",
        )
    )
    assert banter == [("banter", 0, "COMPLETE"), ("banter", 1, "COMPLETE")]

    # 같은 입력 재실행: 새 PREPARE job(새 generation). 조서는 node_results 재사용
    await env.prepare(wired.gateway)

    assert roles(wired.judgment)["context"] == 1
    assert await _context_rows(env.engine) == 1

    # prompt_version 만 바꾸면 키가 달라 다시 부른다
    await env.enqueue("PREPARE", post_id=POST_ID, post_version=1, audience_version=1)
    job = await env.jobs.claim(["PREPARE"], WORKER_ID)
    assert job is not None
    deps = PrepareDeps(
        backend=env.backend,  # type: ignore[arg-type]
        preparation=PostgresPreparation(env.engine),
        settings=settings,
        semaphore=asyncio.Semaphore(settings.MODEL_CONCURRENCY_LIMIT),
        generation_id=job.generation_id or "",
        llm=wired.gateway,
        prompt_version="bundle-wiring-other",
    )
    await run_preparation(job, deps)

    assert roles(wired.judgment)["context"] == 2
    assert await _context_rows(env.engine) == 2


# --- ⑭ hell 별도 검수 모델 --------------------------------------------------------------


@pytest.mark.parametrize("max_tokens", [800, 3000])
async def test_14_hell_별도_검수는_MODEL_EVALUATOR_HELL_모델로_원장에_남는다(
    env: Env, max_tokens: int
):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings(
        MODEL_EVALUATOR_HELL=HELL_MODEL, EVALUATOR_MAX_OUTPUT_TOKENS=max_tokens
    )
    assert settings.MODEL_EVALUATOR_HELL != settings.MODEL_JUDGMENT
    assert price_for(HELL_MODEL) is not None
    cap = HELL_EVAL_CAP_MICRO_USD if max_tokens == 3000 else CASE_CAP_MICRO_USD
    wired = wire(env.engine, settings, hell=True, cap=cap)

    job_id = await env.sentence(wired.gateway, settings=settings)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    if max_tokens == 3000:
        # 고가 모델의 출력 예약액만으로 사건 cap을 넘으면 실제 호출하지 않는다.
        # cap 은 HELL_EVAL_CAP_MICRO_USD 로 박아 둔다. 제품 상한을 따라가면 상한을 올릴 때마다
        # 이 경계가 조용히 사라진다.
        assert env.backend.finalized == []
        assert env.backend.failed == ["BUDGET_EXCEEDED"]
        assert roles(wired.hell) == Counter()
        assert (await _budget(env.engine))["reserved_micro_usd"] == 0
        return
    assert len(env.backend.finalized) == 1
    evaluators = [row for row in await _calls(env.engine) if row["node"] == "evaluator"]
    assert [
        (row["call_index"], row["vendor"], row["model_id"], row["status"]) for row in evaluators
    ] == [
        (0, "openai", settings.MODEL_JUDGMENT, "COMPLETE"),
        (1, "openai", HELL_MODEL, "COMPLETE"),
    ]
    assert roles(wired.hell) == Counter({"evaluator": 1})
    assert roles(wired.judgment) == Counter({"sentencing": 1, "evaluator": 1})


# --- 보정 라운드 call_index 오프셋(코디네이터 결정 9/14) -----------------------------------


async def test_보정_라운드는_call_index_오프셋으로_같은_generation_에서_UNIQUE_충돌_없이_남는다(
    env: Env,
):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    settings = make_settings()
    planned = PlannedLLM(FakeLLM(), evaluator_fails=[{"hell"}, {"hell"}])
    wired = wire(env.engine, settings, judgment=planned)

    job_id = await env.sentence(wired.gateway, settings=settings)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert env.backend.failed == []
    assert len(env.backend.finalized) == 1
    # 서기: 첫 fan-out 0·1, 보정(hell = target 순서 1) 1×3+1 = 4
    # 검수: 라운드 0·1·2 → 0·2·4. 충돌로 폴백했다면 행이 빠지고 finalize 가 달라진다
    assert _slots(await _calls(env.engine)) == [
        ("evaluator", 0, "COMPLETE"),
        ("evaluator", 2, "COMPLETE"),
        ("evaluator", 4, "COMPLETE"),
        ("sentencing", 0, "COMPLETE"),
        ("writer", 0, "COMPLETE"),
        ("writer", 1, "COMPLETE"),
        ("writer", 4, "COMPLETE"),
    ]
    assert planned.roles() == Counter({"sentencing": 1, "evaluator": 3})
    assert roles(wired.writer) == Counter({"writer": 3})
    assert (await _budget(env.engine))["reserved_micro_usd"] == 0
