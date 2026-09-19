"""그래프 B(사전 준비) 통합 테스트(05 §3.2·§4.2, GR-02).

실제 Postgres + FakeLLM + 가짜 백엔드(`ASGITransport`, `ai.jobs` 소유 확인).

① 정상: 조서 1 + 드립 2(2강도), `trial_prep` COMPLETE, dossier·evidence 저장
② Banter 실패 → `DOSSIER_READY`, 조서는 남고 `load_valid_prep` 이 banter 빈 dict 로 돌려준다
③ `source_refs` 밖 fact 삭제
④ 같은 사건 재처리 → 새 행 없음(COMPLETE 불변)
⑤ epoch 불일치 → 저장 0 · `EVIDENCE_INVALIDATED`
⑥ `load_valid_prep`: input_hash·prompt_version·invalidated·privacy_versions 각각 다르면 None
⑦ recall timeout → 빈 후보로 계속(F0 는 있음)
⑧ 구버전 이벤트 → 저장 0 · complete
⑨ 데모 C: `resolve_starbucks` 로 PRIOR·AGGREGATE 가 dossier 에 들어간다
⑩ `ctx.llm` 이 None → 코드 Evidence 만으로 조서 저장, 드립 생략
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.postgres_jobs import PostgresJobs
from geoji_ai.adapters.postgres_memory import PostgresMemory
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.prepare_case import PrepareHandler
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.domain.intensity import Intensity
from geoji_ai.ports.llm import LLMError, LLMPort, LLMResult
from geoji_ai.ports.memory import MemoryCandidate, MemoryPort, RoomRecall
from geoji_ai.prompts import prompt_bundle_version
from geoji_ai.workers.dispatch import HandlerContext
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings

Enqueue = Callable[..., Awaitable[str]]
FetchJob = Callable[[str], Awaitable[dict[str, Any]]]

ROOT = Path(__file__).resolve().parents[2]
TAXI_PATH = ROOT / "contracts" / "fixtures" / "case-snapshot-taxi.json"
STARBUCKS_PATH = ROOT / "contracts" / "fixtures" / "case-snapshot-starbucks-3rd.json"
RESOLVE_STARBUCKS_PATH = ROOT / "tests" / "fakes" / "resolve_starbucks.json"

POST_ID = "post-graph-b"
ROOM_A = "room-ddegeoji-01"
ROOM_B = "room-ddegeoji-02"
WORKER_ID = "test:graph-b:PREPARE"

#: ① 의 조서 출력. F0 을 가리키는 RULE_HIT 하나, REASON_ANALYSIS 하나.
CONTEXT_OK: dict[str, Any] = {
    "facts": [
        {"kind": "RULE_HIT", "text": "택시 규칙에 걸리는 지출이다.", "source_refs": ["F0"]},
        {"kind": "REASON_ANALYSIS", "text": "사유는 편의 목적이다.", "source_refs": ["F0"]},
    ],
    "reason_analysis": {
        "has_mitigation": False,
        "mitigation_kind": None,
        "injection_suspected": False,
    },
}


# --- 준비물 ----------------------------------------------------------------------


def _two_room_snapshot_data() -> dict[str, Any]:
    """택시 스냅샷을 2방(spicy·hell)으로 넓힌다."""
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
    data["jury"] = None
    return data


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "case-snapshot-two-rooms.json"
    path.write_text(json.dumps(_two_room_snapshot_data(), ensure_ascii=False), encoding="utf-8")
    return path


def _snapshot(path: Path, post_id: str = POST_ID, **update: Any) -> CaseSnapshot:
    """가짜 백엔드가 돌려준 것과 같은 스냅샷(post_id 는 job payload 값)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["post_id"] = post_id
    data.update(update)
    return CaseSnapshot.model_validate(data)


@dataclass
class _Backend:
    app: FastAPI
    http: BackendHttp

    @property
    def calls(self) -> list[tuple[str, str]]:
        return self.app.state.fake.calls


async def _backend_for(
    engine: AsyncEngine, snapshot_fixture: Path, resolve_fixture: Path | None = None
) -> AsyncIterator[_Backend]:
    app = create_fake_backend(
        jobs_engine=engine, snapshot_fixture=snapshot_fixture, resolve_fixture=resolve_fixture
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    http = BackendHttp("http://fake-backend", FAKE_SERVICE_TOKEN, client=client)
    try:
        yield _Backend(app, http)
    finally:
        await http.aclose()


@pytest.fixture
async def backend(engine: AsyncEngine, snapshot_path: Path) -> AsyncIterator[_Backend]:
    async for item in _backend_for(engine, snapshot_path):
        yield item


@pytest.fixture
async def epochs(engine: AsyncEngine, privacy_epochs: str, snapshot_path: Path) -> None:
    """스냅샷 epoch 와 같은 현재 epoch(백엔드 자리)."""
    snapshot = _snapshot(snapshot_path)
    await seed_epochs(engine, {pv.scope_key: pv.epoch for pv in snapshot.privacy_versions})


class BanterFailLLM:
    """`role == "banter"` 이면 `LLMError`, 나머지는 안쪽 LLM 에 넘긴다."""

    def __init__(self, inner: FakeLLM) -> None:
        self.inner = inner
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
        if role == "banter":
            raise LLMError("SERVER", message="fake banter failure")
        return await self.inner.structured_call(
            role=role,
            messages=messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )


class HangingMemory:
    """recall 이 끝나지 않는 기억 포트. `RECALL_TIMEOUT_S` 의 `wait_for` 가 끊어야 한다."""

    def __init__(self) -> None:
        self.never = asyncio.Event()

    async def recall_user(
        self,
        user_id: str,
        category: str,
        before: datetime,
        limit: int,
        *,
        reason: str | None = None,
    ) -> list[MemoryCandidate]:
        await self.never.wait()
        return []

    async def recall_room(self, room_id: str, category: str) -> RoomRecall:
        await self.never.wait()
        return RoomRecall()

    async def retain_verdict(self, event_id: str, verdict_payload: dict[str, Any]) -> int:
        return 0

    async def retain_comment(self, event_id: str, comment_payload: dict[str, Any]) -> int:
        return 0

    async def delete_user(self, user_id: str) -> int:
        return 0

    async def delete_room(self, room_id: str) -> int:
        return 0

    async def delete_post(self, post_id: str) -> int:
        return 0


async def run_prepare(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    backend: _Backend,
    llm: LLMPort | None,
    *,
    post_id: str = POST_ID,
    post_version: int = 1,
    audience_version: int = 1,
    memory: MemoryPort | None = None,
) -> str:
    """PREPARE 1건을 넣고 claim 해서 핸들러를 직접 돌린다."""
    job_id = await enqueue(
        "PREPARE", post_id=post_id, post_version=post_version, audience_version=audience_version
    )
    job = await jobs.claim(["PREPARE"], WORKER_ID)
    assert job is not None and job.id == job_id
    settings = make_settings()
    ctx = HandlerContext(
        jobs=jobs,
        semaphore=asyncio.Semaphore(settings.MODEL_CONCURRENCY_LIMIT),
        settings=settings,
        generation_id=job.generation_id or "",
        worker_id=WORKER_ID,
        backend=backend.http,
        memory=memory,
        llm=llm,
        preparation=PostgresPreparation(engine),
    )
    await PrepareHandler()(job, ctx)
    return job_id


async def _count(engine: AsyncEngine, table: str, where: str = "true") -> int:
    async with engine.connect() as conn:
        return int(
            (await conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"))).scalar_one()
        )


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    return {
        table: await _count(engine, table)
        for table in ("ai.trial_prep", "ai.dossiers", "ai.evidence", "ai.evidence_sources")
    }


async def _prep_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT CAST(id AS text) AS id, post_id, status, input_hash, prompt_version, "
                "CAST(dossier_id AS text) AS dossier_id, banter_json FROM ai.trial_prep"
            )
        )
        return [dict(row) for row in result.mappings()]


async def _evidence_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT label, epistemic_type, fact_type, text, scope FROM ai.evidence "
                "ORDER BY CAST(substr(label, 2) AS int)"
            )
        )
        return [dict(row) for row in result.mappings()]


def _roles(llm: FakeLLM) -> list[str]:
    return [call.role for call in llm.calls]


# --- ① 정상 ----------------------------------------------------------------------


async def test_01_정상_조서1_드립2_COMPLETE(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
):
    llm = FakeLLM(outputs={"context": CONTEXT_OK})

    job_id = await run_prepare(engine, jobs, enqueue, backend, llm)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert _roles(llm) == ["context", "banter", "banter"]
    rows = await _prep_rows(engine)
    assert len(rows) == 1
    prep = rows[0]
    assert prep["status"] == "COMPLETE"
    assert prep["post_id"] == POST_ID
    assert prep["prompt_version"] == prompt_bundle_version()
    assert set(prep["banter_json"]) == {"spicy", "hell"}
    assert all(prep["banter_json"][key] for key in ("spicy", "hell"))
    assert await _count(engine, "ai.dossiers") == 1

    evidence = await _evidence_rows(engine)
    assert evidence[0]["label"] == "F0"
    inferred = [row for row in evidence if row["epistemic_type"] == "MODEL_INFERENCE"]
    assert [(row["fact_type"], row["text"]) for row in inferred] == [
        ("RULE", "택시 규칙에 걸리는 지출이다.")
    ]
    # F0 의 scope(ROOMS 두 방)를 물려받는다.
    assert inferred[0]["scope"] == {"visibility": "ROOMS", "room_ids": sorted([ROOM_A, ROOM_B])}
    assert inferred[0]["label"] == f"F{len(evidence) - 1}"
    # REASON_ANALYSIS 는 Evidence 로 저장하지 않는다(002 fact_type CHECK 에 없음).
    assert not any("편의 목적" in row["text"] for row in evidence)
    assert await _count(engine, "ai.evidence_sources") >= len(evidence) - 2


# --- ② Banter 실패해도 조서 사용 --------------------------------------------------


async def test_02_Banter_실패해도_조서는_남고_load_valid_prep_이_돌려준다(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
    snapshot_path: Path,
):
    llm = BanterFailLLM(FakeLLM(outputs={"context": CONTEXT_OK}))

    job_id = await run_prepare(engine, jobs, enqueue, backend, llm)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert llm.roles == ["context", "banter", "banter"]
    rows = await _prep_rows(engine)
    assert len(rows) == 1
    assert rows[0]["status"] == "DOSSIER_READY"
    assert rows[0]["banter_json"] is None
    assert await _count(engine, "ai.dossiers") == 1
    assert await _count(engine, "ai.evidence") >= 1

    prep = await PostgresPreparation(engine).load_valid_prep(
        _snapshot(snapshot_path), prompt_bundle_version()
    )
    assert prep is not None
    assert prep.prep_id == rows[0]["id"]
    assert prep.dossier.dossier_id == rows[0]["dossier_id"]
    assert prep.banter == {}
    assert prep.dossier.facts[0].label == "F0"
    assert set(prep.dossier.label_map) == {fact.label for fact in prep.dossier.facts}


# --- ③ source_refs 밖 fact 삭제 ----------------------------------------------------


async def test_03_source_refs_밖_fact_는_저장하지_않는다(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    backend: _Backend,
    epochs: None,
):
    context = {
        "facts": [
            {"kind": "RULE_HIT", "text": "살아남는 규칙 사실.", "source_refs": ["F0"]},
            {"kind": "MITIGATION", "text": "없는 라벨 사실.", "source_refs": ["F99"]},
            {"kind": "MITIGATION", "text": "일부만 있는 사실.", "source_refs": ["F0", "F42"]},
            {"kind": "PATTERN", "text": "허용 밖 kind 사실.", "source_refs": ["F0"]},
        ],
        "reason_analysis": CONTEXT_OK["reason_analysis"],
    }
    llm = FakeLLM(outputs={"context": context})

    await run_prepare(engine, jobs, enqueue, backend, llm)

    texts = [row["text"] for row in await _evidence_rows(engine)]
    assert "살아남는 규칙 사실." in texts
    assert "없는 라벨 사실." not in texts
    assert "일부만 있는 사실." not in texts
    assert "허용 밖 kind 사실." not in texts
    assert await _count(engine, "ai.evidence", "epistemic_type = 'MODEL_INFERENCE'") == 1


# --- ④ 재처리 불변 ----------------------------------------------------------------


async def test_04_같은_사건_재처리는_새_행이_없고_COMPLETE_불변(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
):
    await run_prepare(engine, jobs, enqueue, backend, FakeLLM(outputs={"context": CONTEXT_OK}))
    first_rows = await _prep_rows(engine)
    first_counts = await _counts(engine)

    second_llm = FakeLLM(outputs={"context": CONTEXT_OK})
    job_id = await run_prepare(engine, jobs, enqueue, backend, second_llm)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await _prep_rows(engine) == first_rows
    assert first_rows[0]["status"] == "COMPLETE"
    assert await _counts(engine) == first_counts
    # 이미 COMPLETE 면 드립을 다시 부르지 않는다.
    assert "banter" not in _roles(second_llm)


async def test_04b_DOSSIER_READY_재처리는_같은_행으로_드립부터_잇는다(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
):
    await run_prepare(
        engine, jobs, enqueue, backend, BanterFailLLM(FakeLLM(outputs={"context": CONTEXT_OK}))
    )
    first_rows = await _prep_rows(engine)
    assert first_rows[0]["status"] == "DOSSIER_READY"
    first_dossiers = await _count(engine, "ai.dossiers")
    first_evidence = await _count(engine, "ai.evidence")

    second_llm = FakeLLM(outputs={"context": CONTEXT_OK})
    job_id = await run_prepare(engine, jobs, enqueue, backend, second_llm)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    rows = await _prep_rows(engine)
    assert len(rows) == 1
    assert rows[0]["id"] == first_rows[0]["id"]
    assert rows[0]["dossier_id"] == first_rows[0]["dossier_id"]
    assert rows[0]["status"] == "COMPLETE"
    assert rows[0]["banter_json"] is not None
    assert await _count(engine, "ai.dossiers") == first_dossiers
    assert await _count(engine, "ai.evidence") == first_evidence
    assert _roles(second_llm).count("banter") == 2


# --- ⑤ epoch 불일치 --------------------------------------------------------------


async def test_05_epoch_불일치면_저장_0_과_EVIDENCE_INVALIDATED(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    privacy_epochs: str,
    snapshot_path: Path,
):
    snapshot = _snapshot(snapshot_path)
    current = {pv.scope_key: pv.epoch for pv in snapshot.privacy_versions}
    current[f"room:{ROOM_B}"] += 1
    await seed_epochs(engine, current)
    llm = FakeLLM(outputs={"context": CONTEXT_OK})

    job_id = await run_prepare(engine, jobs, enqueue, backend, llm)

    row = await fetch_job(job_id)
    assert row["last_error_code"] == "EVIDENCE_INVALIDATED"
    assert row["status"] == "QUEUED"
    assert row["owner_id"] is None
    assert await _counts(engine) == dict.fromkeys(
        ("ai.trial_prep", "ai.dossiers", "ai.evidence", "ai.evidence_sources"), 0
    )
    assert "banter" not in _roles(llm)


# --- ⑥ load_valid_prep 조건 --------------------------------------------------------


async def _break_invalidated(engine: AsyncEngine, snapshot: CaseSnapshot) -> CaseSnapshot:
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE ai.trial_prep SET invalidated_at = now()"))
    return snapshot


async def _break_privacy(engine: AsyncEngine, snapshot: CaseSnapshot) -> CaseSnapshot:
    changed = [
        {"scope_key": pv.scope_key, "epoch": pv.epoch + 1} for pv in snapshot.privacy_versions
    ]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE ai.dossiers SET privacy_versions = CAST(:pv AS jsonb)"),
            {"pv": json.dumps(changed)},
        )
    return snapshot


async def _break_input_hash(engine: AsyncEngine, snapshot: CaseSnapshot) -> CaseSnapshot:
    return snapshot.model_copy(update={"reason": "다른 사유"})


async def _keep(engine: AsyncEngine, snapshot: CaseSnapshot) -> CaseSnapshot:
    return snapshot


@pytest.mark.parametrize(
    ("breaker", "prompt_version", "found"),
    [
        (_keep, None, True),
        (_break_input_hash, None, False),
        (_keep, "bundle-other", False),
        (_break_invalidated, None, False),
        (_break_privacy, None, False),
    ],
    ids=["일치", "input_hash_다름", "prompt_version_다름", "invalidated", "privacy_versions_다름"],
)
async def test_06_load_valid_prep_조건(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    backend: _Backend,
    epochs: None,
    snapshot_path: Path,
    breaker: Callable[[AsyncEngine, CaseSnapshot], Awaitable[CaseSnapshot]],
    prompt_version: str | None,
    found: bool,
):
    await run_prepare(engine, jobs, enqueue, backend, FakeLLM(outputs={"context": CONTEXT_OK}))
    snapshot = await breaker(engine, _snapshot(snapshot_path))

    prep = await PostgresPreparation(engine).load_valid_prep(
        snapshot, prompt_version or prompt_bundle_version()
    )

    assert (prep is not None) is found
    if prep is not None:
        assert set(prep.banter) == {Intensity.spicy, Intensity.hell}
        candidate = prep.banter[Intensity.hell][0]
        assert candidate.candidate_id and candidate.text
        assert set(candidate.fits) <= {"guilty", "notGuilty", "agree", "disagree"}


# --- ⑦ recall timeout --------------------------------------------------------------


async def test_07_recall_timeout_이면_빈_후보로_계속(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
    monkeypatch: pytest.MonkeyPatch,
):
    requests: list[Any] = []
    resolve = backend.http.resolve_evidence

    async def spy_resolve(job_id: str, generation_id: str, request: Any) -> Any:
        requests.append(request)
        return await resolve(job_id, generation_id, request)

    monkeypatch.setattr(backend.http, "resolve_evidence", spy_resolve)

    # `wait_for` 가 빠지면 멈추지 않고 실패하도록 바깥 상한을 둔다.
    job_id = await asyncio.wait_for(
        run_prepare(
            engine,
            jobs,
            enqueue,
            backend,
            FakeLLM(outputs={"context": CONTEXT_OK}),
            memory=HangingMemory(),
        ),
        timeout=10,
    )

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert (await _prep_rows(engine))[0]["status"] == "COMPLETE"
    evidence = await _evidence_rows(engine)
    assert evidence[0]["label"] == "F0"
    assert evidence[0]["fact_type"] == "SPEND"
    assert len(requests) == 1
    assert requests[0].candidates == []


# --- ⑧ 구버전 이벤트 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "versions",
    [{"post_version": 2}, {"audience_version": 2}],
    ids=["post_version", "audience_version"],
)
async def test_08_구버전_이벤트는_저장없이_complete(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
    versions: dict[str, int],
):
    llm = FakeLLM(outputs={"context": CONTEXT_OK})

    job_id = await run_prepare(engine, jobs, enqueue, backend, llm, **versions)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    assert await _count(engine, "ai.trial_prep") == 0
    assert await _count(engine, "ai.dossiers") == 0
    assert llm.calls == []
    assert [path.rsplit("/", 1)[-1] for _, path in backend.calls] == ["snapshot"]


# --- ⑨ 데모 C ----------------------------------------------------------------------


async def test_09_데모C_PRIOR_와_AGGREGATE_가_dossier_에_들어간다(
    engine: AsyncEngine,
    privacy_epochs: str,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
):
    starbucks = CaseSnapshot.model_validate(json.loads(STARBUCKS_PATH.read_text(encoding="utf-8")))
    await seed_epochs(engine, {pv.scope_key: pv.epoch for pv in starbucks.privacy_versions})
    llm = FakeLLM(
        outputs={"context": {"facts": [], "reason_analysis": CONTEXT_OK["reason_analysis"]}}
    )

    async for demo in _backend_for(engine, STARBUCKS_PATH, RESOLVE_STARBUCKS_PATH):
        job_id = await run_prepare(
            engine,
            jobs,
            enqueue,
            demo,
            llm,
            post_id=starbucks.post_id,
            memory=PostgresMemory(engine, make_settings()),
        )

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    evidence = await _evidence_rows(engine)
    fact_types = [row["fact_type"] for row in evidence]
    assert fact_types.count("AGGREGATE") == 2
    assert fact_types.count("VERDICT") == 2
    assert "RULE" in fact_types  # "카페는 주 1회까지" 가 카테고리 토큰 "카페" 에 적중
    assert any("2건" in row["text"] for row in evidence if row["fact_type"] == "AGGREGATE")
    rows = await _prep_rows(engine)
    assert [row["status"] for row in rows] == ["COMPLETE"]
    assert set(rows[0]["banter_json"]) == {"spicy"}
    assert _roles(llm) == ["context", "banter"]


# --- ⑩ LLM 없음 ----------------------------------------------------------------------


async def test_10_llm_이_없으면_코드_Evidence_만_저장하고_드립_생략(
    engine: AsyncEngine,
    jobs: PostgresJobs,
    enqueue: Enqueue,
    fetch_job: FetchJob,
    backend: _Backend,
    epochs: None,
):
    job_id = await run_prepare(engine, jobs, enqueue, backend, None)

    assert (await fetch_job(job_id))["status"] == "SUCCEEDED"
    rows = await _prep_rows(engine)
    assert [(row["status"], row["banter_json"]) for row in rows] == [("DOSSIER_READY", None)]
    assert await _count(engine, "ai.evidence", "epistemic_type = 'MODEL_INFERENCE'") == 0
    assert await _count(engine, "ai.evidence") >= 1
