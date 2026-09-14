"""그래프 C(선고) 통합 흐름(05 §4.2 GR-08, GR-06·GR-03).

실제 Postgres + 가짜 백엔드(`ASGITransport`, `ai.jobs` 소유 확인) + FakeLLM + dispatch 핸들러.

먼저 실패시킬 것(05 §4.2):
① Banter 실패해도 조서 사용 — `DOSSIER_READY` prep 으로 C 가 돈다, 서기 후보 `[]`
② prep 없을 때 — 남은 8.4s → MINIMAL(조서 0), 남은 9s → INLINE(조서 1)
③ 검수 실패 2회 → repair 1회 뒤 실패 강도 TEMPLATE, generation_failed 아님
④ 강도 일부 누락 → join 실패(서버 검증 5항)
⑤ 서기 병렬 중 1개만 실패 → 부분 TEMPLATE

추가: ⑥ finalize 422 → repair 1회 뒤 성공 ⑦ `MODEL_EVALUATOR_HELL` 이 다르면 hell 별도 검수
⑧ finalize 직전 epoch 증가 → finalize 0·`EVIDENCE_INVALIDATED` ⑨ 워커 dispatch 경로 SENTENCE →
finalize 1회·SUCCEEDED ⑩ TEXT_RETRY → 양형 0·형량 불변 ⑪ 유죄·2강도·보정 없음 = 양형 1 + 서기 2 +
검수 1
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.fake_llm import FakeLLM, FakeScenario
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.sentence_case import SentenceHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.contracts.finalize import FinalizeRequest
from geoji_ai.core.config import Settings
from geoji_ai.domain.attack_angles import pick
from geoji_ai.graphs.sentencing import build_sentence_graph
from geoji_ai.ports.llm import LLMResult
from geoji_ai.workers.dispatch import HandlerContext, handler_for
from geoji_ai.workers.main import Worker
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration.test_graph_b import CONTEXT_OK, BanterFailLLM
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings, run_worker_until

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

ROOT = Path(__file__).resolve().parents[2]
TAXI_PATH = ROOT / "contracts" / "fixtures" / "case-snapshot-taxi.json"

POST_ID = "post-graph-c"
VERDICT_ID = "verdict-graph-c"
ROOM_A = "room-ddegeoji-01"
ROOM_B = "room-ddegeoji-02"
WORKER_ID = "test:graph-c"
DEFAULT_REMAINING_S = 60.0


# --- 준비물 ----------------------------------------------------------------------


def _snapshot_data() -> dict[str, Any]:
    """택시 스냅샷을 2방(spicy·hell) · 유죄 · 2강도로."""
    data = json.loads(TAXI_PATH.read_text(encoding="utf-8"))
    data["post_id"] = POST_ID
    data["audience"]["room_ids"] = [ROOM_A, ROOM_B]
    data["privacy_versions"] = [
        {"scope_key": f"user:{data['author_id']}", "epoch": 1},
        {"scope_key": f"room:{ROOM_A}", "epoch": 1},
        {"scope_key": f"room:{ROOM_B}", "epoch": 1},
    ]
    data["room_snapshots"] = [
        {"room_id": ROOM_A, "intensity": "spicy", "rule_version": 1},
        {"room_id": ROOM_B, "intensity": "hell", "rule_version": 1},
    ]
    data["jury"]["target_intensities"] = ["spicy", "hell"]
    return data


class SpyBackend:
    """`BackendHttp` 감싸개. finalize 요청·generation-failed 코드를 기록하고 그대로 넘긴다."""

    def __init__(self, inner: BackendHttp) -> None:
        self.inner = inner
        self.finalized: list[FinalizeRequest] = []
        self.failed: list[str] = []

    async def finalize(self, verdict_id: str, req: FinalizeRequest) -> Any:
        self.finalized.append(req)
        return await self.inner.finalize(verdict_id, req)

    async def generation_failed(self, verdict_id: str, **kwargs: Any) -> Any:
        self.failed.append(kwargs["error_code"])
        return await self.inner.generation_failed(verdict_id, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class PlannedLLM:
    """FakeLLM 앞단. 검수관 k번째 호출에서 `evaluator_fails[k]` 강도를 `pass=false` 로 돌려준다.

    `before(role)` 는 호출 직전 훅(비동기)이다.
    """

    def __init__(
        self,
        inner: Any | None = None,
        *,
        evaluator_fails: Sequence[set[str]] = (),
        before: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.inner = inner or FakeLLM()
        self.evaluator_fails = [set(item) for item in evaluator_fails]
        self.before = before
        self.calls: list[tuple[str, dict, list[dict]]] = []

    async def structured_call(
        self,
        *,
        role: Any,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        self.calls.append((role, schema, messages))
        if self.before is not None:
            await self.before(role)
        if role == "evaluator":
            index = sum(1 for call in self.calls if call[0] == "evaluator") - 1
            fails = self.evaluator_fails[index] if index < len(self.evaluator_fails) else set()
            result = await self.inner.structured_call(
                role=role,
                messages=messages,
                schema=schema,
                timeout_s=timeout_s,
                max_output_tokens=max_output_tokens,
            )
            return replace(result, output=_fail_texts(result.output, fails))
        return await self.inner.structured_call(
            role=role,
            messages=messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )

    def roles(self) -> Counter[str]:
        return Counter(call[0] for call in self.calls)

    def of(self, role: str) -> list[tuple[str, dict, list[dict]]]:
        return [call for call in self.calls if call[0] == role]


def _fail_texts(output: dict | None, fails: set[str]) -> dict | None:
    if output is None or not fails:
        return output
    report = dict(output)
    texts = []
    for index, entry in enumerate(report["texts"]):
        entry = dict(entry)
        if entry["intensity"] in fails:
            entry["pass"] = False
            entry["violations"] = [
                {
                    "code": "PERSONAL_ATTACK",
                    "path": f"texts[{index}].statement[0].text",
                    "evidence_labels": [],
                    "explanation": "fake 위반",
                }
            ]
            entry["problem_sentences"] = ["문제 문장 마커"]
        texts.append(entry)
    report["texts"] = texts
    return report


def _user_payload(messages: list[dict]) -> dict[str, Any]:
    return json.loads(messages[-1]["content"])


def _enum(schema: dict, *path: str) -> list[str]:
    node: Any = schema
    for key in path:
        node = node[key]
    return list(node["enum"])


@dataclass
class Env:
    engine: AsyncEngine
    jobs: PostgresJobs
    enqueue: Enqueue
    fetch_job: FetchJob
    app: FastAPI
    backend: SpyBackend
    job_ids: dict[str, str] = field(default_factory=dict)

    @property
    def fake(self) -> Any:
        return self.app.state.fake

    def paths(self) -> list[str]:
        return [path.rsplit("/", 1)[-1] for _, path in self.fake.calls]

    def ctx(self, job: Any, llm: Any, settings: Settings | None = None) -> HandlerContext:
        settings = settings or make_settings()
        return HandlerContext(
            jobs=self.jobs,
            semaphore=asyncio.Semaphore(settings.MODEL_CONCURRENCY_LIMIT),
            settings=settings,
            generation_id=job.generation_id or "",
            worker_id=WORKER_ID,
            backend=self.backend,  # type: ignore[arg-type]
            llm=llm,
            preparation=PostgresPreparation(self.engine),
        )

    async def prepare(self, llm: Any) -> str:
        job_id = await self.enqueue("PREPARE", post_id=POST_ID, post_version=1, audience_version=1)
        job = await self.jobs.claim(["PREPARE"], WORKER_ID)
        assert job is not None and job.id == job_id
        await handler_for("PREPARE")(job, self.ctx(job, llm))
        return job_id

    async def sentence(
        self,
        llm: Any,
        *,
        remaining_s: float = DEFAULT_REMAINING_S,
        settings: Settings | None = None,
        handler: Any | None = None,
        kind: str = "SENTENCE",
        intensities: list[str] | None = None,
    ) -> str:
        if kind == "SENTENCE":
            job_id = await self.enqueue(
                "SENTENCE", verdict_id=VERDICT_ID, verdict_version=1, post_id=POST_ID
            )
        else:
            extra: dict[str, Any] = {} if intensities is None else {"intensities": intensities}
            job_id = await self.enqueue(
                "TEXT_RETRY", verdict_id=VERDICT_ID, verdict_version=1, round=1, **extra
            )
        job = await self.jobs.claim([kind], WORKER_ID)
        assert job is not None and job.id == job_id
        if VERDICT_ID not in self.fake.verdicts:
            # 남은 시간은 claim 이 찍은 DB 시각(`updated_at`) 기준이다.
            self.fake.seed_verdict(
                VERDICT_ID,
                verdict_version=1,
                post_id=POST_ID,
                deadline_at=job.updated_at + timedelta(seconds=remaining_s),
            )
        await (handler or handler_for(kind))(job, self.ctx(job, llm, settings))
        return job_id


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "case-snapshot-graph-c.json"
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


async def _rows(engine: AsyncEngine, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(row) for row in (await conn.execute(text(sql), params)).mappings()]


async def _dossier_ids(engine: AsyncEngine) -> list[str]:
    rows = await _rows(engine, "SELECT CAST(id AS text) AS id FROM ai.dossiers ORDER BY created_at")
    return [row["id"] for row in rows]


async def _labels(engine: AsyncEngine, dossier_id: str) -> list[tuple[str, str]]:
    rows = await _rows(
        engine,
        "SELECT label, epistemic_type FROM ai.evidence WHERE dossier_id = CAST(:d AS uuid) "
        "ORDER BY CAST(substr(label, 2) AS int)",
        d=dossier_id,
    )
    return [(row["label"], row["epistemic_type"]) for row in rows]


def _sources(req: FinalizeRequest) -> list[tuple[str, str]]:
    return [(str(t.intensity), t.source) for t in req.draft.texts]


# --- ① Banter 실패해도 조서 사용 --------------------------------------------------


async def test_01_Banter_실패해도_DOSSIER_READY_조서로_선고한다(env: Env):
    await env.prepare(BanterFailLLM(FakeLLM(outputs={"context": CONTEXT_OK})))
    prep = await _rows(
        env.engine, "SELECT status, CAST(dossier_id AS text) AS dossier_id FROM ai.trial_prep"
    )
    assert [row["status"] for row in prep] == ["DOSSIER_READY"]
    env.fake.calls.clear()

    llm = PlannedLLM()
    job_id = await env.sentence(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert "context" not in llm.roles()
    assert "resolve-evidence" not in env.paths()
    writers = llm.of("writer")
    assert len(writers) == 2
    assert all(_user_payload(messages)["banter_candidates"] == [] for _, _, messages in writers)
    assert len(env.backend.finalized) == 1
    assert env.backend.finalized[0].dossier_id == prep[0]["dossier_id"]
    assert env.backend.failed == []
    assert env.fake.verdicts[VERDICT_ID].sentence_status == "FINAL"


# --- ② prep 없을 때 ---------------------------------------------------------------


#: D-25(9/14) 로 서기 상한 + 검수 상한 + finalize 예약을 남긴 뒤에만 즉석 조서를 부른다. 기본 상한
#: (6·4)에서는 10초 안에 들어가지 않아, INLINE 경로는 상한을 낮춘 설정으로 본다(제품 값 아님).
_LOW_CAPS: dict[str, Any] = {"WRITER_NODE_TIMEOUT_SECONDS": 2, "EVALUATOR_NODE_TIMEOUT_SECONDS": 2}


@pytest.mark.parametrize(
    ("remaining_s", "caps", "source"),
    [(8.4, {}, "MINIMAL"), (10.0, {}, "MINIMAL"), (10.0, _LOW_CAPS, "INLINE")],
    ids=["8.4s_MINIMAL", "기본상한_10s_MINIMAL", "서기2_검수2_10s_INLINE"],
)
async def test_02_prep_없으면_남은시간으로_MINIMAL_또는_INLINE(
    env: Env, remaining_s: float, caps: dict[str, Any], source: str
):
    llm = PlannedLLM(FakeLLM(outputs={"context": CONTEXT_OK}))

    job_id = await env.sentence(llm, remaining_s=remaining_s, settings=make_settings(**caps))

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    # MINIMAL·INLINE 모두 dossier 를 저장한다. trial_prep 은 만들지 않는다.
    dossier_ids = await _dossier_ids(env.engine)
    assert len(dossier_ids) == 1
    assert await _rows(env.engine, "SELECT id FROM ai.trial_prep") == []
    labels = await _labels(env.engine, dossier_ids[0])
    if source == "MINIMAL":
        assert llm.roles()["context"] == 0
        assert "resolve-evidence" not in env.paths()
        assert [label for label, _ in labels] == ["F0"]
        # 서기 fixture 문장은 F0 밖 라벨을 인용해 ⑤ 에서 걸린다(fixture 한계, 01 소유).
        # 그래서 finalize 없이 EVAL_FAILED 로 끝나는 것까지 고정한다.
        assert env.backend.finalized == []
        assert env.backend.failed == ["EVAL_FAILED"]
    else:
        assert llm.roles()["context"] == 1
        assert "resolve-evidence" in env.paths()
        assert "MODEL_INFERENCE" in {kind for _, kind in labels}
        assert [req.dossier_id for req in env.backend.finalized] == dossier_ids
        assert env.backend.failed == []


# --- ③ 검수 실패 2회 → repair 1회 뒤 TEMPLATE ------------------------------------


async def test_03_검수_실패_2회면_repair_1회_뒤_그_강도만_TEMPLATE(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    llm = PlannedLLM(evaluator_fails=[{"hell"}, {"hell"}])

    job_id = await env.sentence(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    writers = llm.of("writer")
    # 첫 fan-out 2 + repair(hell) 1
    assert len(writers) == 3
    repair_schema, repair_messages = writers[2][1], writers[2][2]
    assert _enum(repair_schema, "properties", "intensity") == ["hell"]
    repair_input = _user_payload(repair_messages)
    assert repair_input["attack_angle"]["code"] == pick(POST_ID, 1).value
    assert _enum(writers[0][1], "properties", "attack_angle") == [pick(POST_ID, 0).value]
    assert "PERSONAL_ATTACK" in json.dumps(repair_input["avoid"], ensure_ascii=False)
    assert "문제 문장 마커" in json.dumps(repair_input["avoid"], ensure_ascii=False)

    evaluators = llm.of("evaluator")
    # 전 강도 → 실패 강도만(repair) → TEMPLATE 치환 뒤 재검수(hash 규칙)
    assert len(evaluators) == 3
    assert _enum(evaluators[1][1], "properties", "texts", "items", "properties", "intensity") == [
        "hell"
    ]
    assert env.backend.failed == []
    assert len(env.backend.finalized) == 1
    req = env.backend.finalized[0]
    assert _sources(req) == [("spicy", "AI"), ("hell", "TEMPLATE")]
    assert llm.roles()["sentencing"] == 1
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_source, verdict.text_status) == ("AI", "TEMPLATE_READY")


# --- ④ 강도 일부 누락 → join 실패 ---------------------------------------------------


NO_TEMPLATES: dict[str, Any] = {"results": {}, "sentence_labels": {}}


async def test_04_강도_일부_누락이면_join_에서_실패한다(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    # hell 서기 실패 + 템플릿 없음 → drafts 에 hell 이 빠진다.
    handler = SentenceHandler(
        lambda deps: build_sentence_graph(replace(deps, templates=NO_TEMPLATES))
    )
    llm = PlannedLLM(FakeLLM(FakeScenario.INTENSITY_FAIL))

    job_id = await env.sentence(llm, handler=handler)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert llm.roles()["evaluator"] == 0
    assert env.backend.finalized == []
    assert env.backend.failed == ["SCHEMA_INVALID"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source) == ("FINAL", "RULE")


# --- ⑤ 서기 1개만 실패 → 부분 TEMPLATE ---------------------------------------------


async def test_05_서기_병렬_중_hell_만_실패하면_부분_TEMPLATE(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    llm = PlannedLLM(FakeLLM(FakeScenario.INTENSITY_FAIL))

    job_id = await env.sentence(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert llm.roles()["writer"] == 2
    assert llm.roles()["evaluator"] == 1
    assert env.backend.failed == []
    assert len(env.backend.finalized) == 1
    assert _sources(env.backend.finalized[0]) == [("spicy", "AI"), ("hell", "TEMPLATE")]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source, verdict.text_status) == (
        "FINAL",
        "AI",
        "TEMPLATE_READY",
    )
    retain = await _rows(env.engine, "SELECT event_type FROM ai.jobs WHERE kind = 'RETAIN'")
    assert [row["event_type"] for row in retain] == ["sentence.finalized"]


# --- ⑥ finalize 422 → repair 1회 뒤 성공 -------------------------------------------


async def test_06_finalize_422_이면_repair_1회_뒤_성공(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    env.fake.finalize_rejections.append((422, "INVALID_DRAFT"))
    llm = PlannedLLM()

    job_id = await env.sentence(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert len(env.backend.finalized) == 2
    assert llm.roles() == Counter({"sentencing": 1, "writer": 4, "evaluator": 2})
    repair_angles = [
        _enum(schema, "properties", "attack_angle") for _, schema, _ in llm.of("writer")[2:]
    ]
    assert repair_angles == [[pick(POST_ID, 1).value]] * 2
    first, second = env.backend.finalized
    assert first.sentencing == second.sentencing  # 형량 불변
    assert _sources(second) == [("spicy", "AI"), ("hell", "AI")]
    assert env.backend.failed == []
    assert env.fake.verdicts[VERDICT_ID].text_status == "AI_READY"


# --- ⑦ hell 별도 검수 ---------------------------------------------------------------


async def test_07_MODEL_EVALUATOR_HELL_이_다르면_hell_만_별도_검수(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    llm = PlannedLLM()
    settings = make_settings(MODEL_EVALUATOR_HELL="fake-evaluator-hell")
    assert settings.MODEL_EVALUATOR_HELL != settings.MODEL_JUDGMENT

    job_id = await env.sentence(llm, settings=settings)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    evaluators = llm.of("evaluator")
    assert len(evaluators) == 2
    enums = sorted(
        _enum(schema, "properties", "texts", "items", "properties", "intensity")
        for _, schema, _ in evaluators
    )
    assert enums == [["hell"], ["spicy"]]
    for _, schema, messages in evaluators:
        wanted = _enum(schema, "properties", "texts", "items", "properties", "intensity")
        drafted = [t["intensity"] for t in _user_payload(messages)["draft"]["texts"]]
        assert drafted == wanted
    assert len(env.backend.finalized) == 1
    req = env.backend.finalized[0]
    assert [str(t.intensity) for t in req.evaluation.texts] == ["spicy", "hell"]
    assert env.backend.failed == []


# --- ⑧ epoch 증가 뒤 finalize 0 -------------------------------------------------------


async def test_08_finalize_직전_epoch_가_바뀌면_finalize_없이_EVIDENCE_INVALIDATED(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))

    async def bump(role: str) -> None:
        if role == "evaluator":
            await seed_epochs(env.engine, {f"room:{ROOM_B}": 2})

    llm = PlannedLLM(before=bump)

    job_id = await env.sentence(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert llm.roles()["evaluator"] == 1
    assert env.backend.finalized == []
    assert "finalize" not in env.paths()
    assert env.backend.failed == ["EVIDENCE_INVALIDATED"]
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source) == ("FINAL", "RULE")
    assert env.fake.text_retry_rounds == []


# --- ⑨ 워커 dispatch 경로 -------------------------------------------------------------


async def test_09_워커가_SENTENCE_job_을_finalize_1회로_끝낸다(env: Env):
    env.fake.seed_verdict(
        VERDICT_ID,
        verdict_version=1,
        post_id=POST_ID,
        deadline_at=datetime.now(UTC) + timedelta(seconds=DEFAULT_REMAINING_S),
    )
    job_id = await env.enqueue(
        "SENTENCE", verdict_id=VERDICT_ID, verdict_version=1, post_id=POST_ID
    )
    worker = Worker(
        env.jobs,
        make_settings(WORKER_SLOTS={"SENTENCE": 1}),
        backend=env.backend,  # type: ignore[arg-type]
        llm=FakeLLM(outputs={"context": CONTEXT_OK}),
        preparation=PostgresPreparation(env.engine),
    )

    async def done() -> bool:
        return (await env.fetch_job(job_id))["status"] in {"SUCCEEDED", "FAILED"}

    await run_worker_until(worker, done)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert env.paths().count("finalize") == 1
    assert env.backend.failed == []
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_status, verdict.sentence_source) == ("FINAL", "AI")


# --- ⑩ TEXT_RETRY: 양형 0·형량 불변 -------------------------------------------------


async def test_10_TEXT_RETRY_는_양형을_부르지_않고_형량이_그대로다(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    await env.sentence(PlannedLLM(FakeLLM(FakeScenario.INTENSITY_FAIL)))
    verdict = env.fake.verdicts[VERDICT_ID]
    assert (verdict.sentence_source, verdict.text_status) == ("AI", "TEMPLATE_READY")
    before = (verdict.sentence, verdict.sentencing_reason, verdict.text_version)
    env.backend.finalized.clear()

    llm = PlannedLLM()
    retry_id = await env.sentence(llm, kind="TEXT_RETRY")

    assert (await env.fetch_job(retry_id))["status"] == "SUCCEEDED"
    assert llm.roles()["sentencing"] == 0
    assert llm.roles()["writer"] == 2
    assert len(env.backend.finalized) == 1
    req = env.backend.finalized[0]
    assert req.sentencing is not None
    assert (req.sentencing.sentence, req.sentencing.sentencing_reason) == before[:2]
    assert (verdict.sentence, verdict.sentencing_reason) == before[:2]
    assert verdict.text_version == before[2] + 1
    assert verdict.text_status == "AI_READY"


# --- ⑪ 호출 수 ----------------------------------------------------------------------


async def test_11_유죄_2강도_보정_없음_호출수는_양형1_서기2_검수1(env: Env):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    prep = await _rows(env.engine, "SELECT status FROM ai.trial_prep")
    assert [row["status"] for row in prep] == ["COMPLETE"]
    llm = PlannedLLM()

    job_id = await env.sentence(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert llm.roles() == Counter({"sentencing": 1, "writer": 2, "evaluator": 1})
    assert len(env.backend.finalized) == 1
    assert _sources(env.backend.finalized[0]) == [("spicy", "AI"), ("hell", "AI")]
