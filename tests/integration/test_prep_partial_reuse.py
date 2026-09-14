"""준비 자료 부분 재사용(9/14 D-27, 05 §3.2·§3.3). 실제 Postgres + 가짜 백엔드 + FakeLLM.

조서 키(게시물 필드·심문 결과·방 규칙 버전)는 `ai.dossiers.snapshot_hash`, 드립 키(조서 id + 강도 +
prompt_version)는 `ai.trial_prep.banter_json` 의 강도별 `{key, candidates}` 에 둔다(기존 컬럼).
epoch 가 바뀐 것은 부분 재사용하지 않는다.

⑦ 방 강도만 변경 → PREPARE 재실행에서 조서 호출 0 · 새 강도 드립 호출만 · 새 행이 이전 조서를 가리킴
⑧ 방 규칙 버전 변경 → 조서부터 다시
⑨ privacy epoch 변경 → 조서 재사용 0
⑩ SENTENCE 가 부분 일치 prep 에서 조서 사용 · 키 없는 강도는 후보 없이 서기
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
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
from geoji_ai.domain.input_hash import banter_key
from geoji_ai.prompts import prompt_bundle_version
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration.test_graph_b import CONTEXT_OK
from tests.integration.test_graph_c_flow import (
    ROOM_A,
    ROOM_B,
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
Mutate = Callable[[dict[str, Any]], None]


@pytest.fixture
def snapshot_path(tmp_path: Path) -> Path:
    path = tmp_path / "case-snapshot-partial-reuse.json"
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


def _rewrite(path: Path, mutate: Mutate) -> CaseSnapshot:
    """가짜 백엔드가 다음 요청부터 돌려줄 스냅샷을 바꾼다(백엔드 쪽 변경 자리)."""
    data = _snapshot_data()
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return CaseSnapshot.model_validate(data)


def _room(data: dict[str, Any], room_id: str) -> dict[str, Any]:
    return next(room for room in data["room_snapshots"] if room["room_id"] == room_id)


def _room_b_mild(data: dict[str, Any]) -> None:
    _room(data, ROOM_B)["intensity"] = "mild"


async def _prep_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    return await _rows(
        engine,
        "SELECT CAST(id AS text) AS id, status, input_hash, "
        "CAST(dossier_id AS text) AS dossier_id, banter_json "
        "FROM ai.trial_prep ORDER BY created_at",
    )


async def _dossier_count(engine: AsyncEngine) -> int:
    return len(await _rows(engine, "SELECT id FROM ai.dossiers"))


def _roles(llm: FakeLLM) -> Counter[str]:
    return Counter(call.role for call in llm.calls)


def _user(messages: list[dict]) -> dict[str, Any]:
    return json.loads(messages[-1]["content"])


# --- ⑦ 방 강도만 변경 --------------------------------------------------------------------


async def test_07_방_강도만_바뀌면_조서_호출_0_바뀐_강도_드립만_새로(env: Env, snapshot_path: Path):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    [first] = await _prep_rows(env.engine)
    assert first["status"] == "COMPLETE"
    assert set(first["banter_json"]) == {"spicy", "hell"}
    _rewrite(snapshot_path, _room_b_mild)
    env.fake.calls.clear()

    llm = FakeLLM(outputs={"context": CONTEXT_OK})
    job_id = await env.prepare(llm)

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    assert _roles(llm) == Counter({"banter": 1})
    assert _user(llm.calls[0].messages)["intensity"] == "mild"
    # 조서를 다시 만들지 않으므로 근거 조회도 없다.
    assert env.paths() == ["snapshot"]
    rows = await _prep_rows(env.engine)
    assert len(rows) == 2
    old, new = rows
    assert old == first  # 완료분 불변
    assert new["status"] == "COMPLETE"
    assert new["input_hash"] != old["input_hash"]
    assert new["dossier_id"] == old["dossier_id"]
    assert await _dossier_count(env.engine) == 1
    assert set(new["banter_json"]) == {"spicy", "mild"}
    assert new["banter_json"]["spicy"] == old["banter_json"]["spicy"]
    version = prompt_bundle_version()
    for intensity, entry in new["banter_json"].items():
        assert entry["key"] == banter_key(new["dossier_id"], intensity, version)
        assert entry["candidates"]


# --- ⑧ 규칙 버전 변경 --------------------------------------------------------------------


async def test_08_방_규칙_버전이_바뀌면_조서부터_다시(env: Env, snapshot_path: Path):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))

    def bump_rule(data: dict[str, Any]) -> None:
        _room(data, ROOM_A)["rule_version"] = 2

    _rewrite(snapshot_path, bump_rule)
    llm = FakeLLM(outputs={"context": CONTEXT_OK})

    await env.prepare(llm)

    assert _roles(llm) == Counter({"context": 1, "banter": 2})
    old, new = await _prep_rows(env.engine)
    assert new["status"] == "COMPLETE"
    assert new["dossier_id"] != old["dossier_id"]
    assert await _dossier_count(env.engine) == 2


# --- ⑨ epoch 변경 -------------------------------------------------------------------------


async def test_09_privacy_epoch_가_바뀌면_조서를_재사용하지_않는다(env: Env, snapshot_path: Path):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))

    def bump_epoch(data: dict[str, Any]) -> None:
        for item in data["privacy_versions"]:
            if item["scope_key"] == f"room:{ROOM_B}":
                item["epoch"] = 2

    _rewrite(snapshot_path, bump_epoch)
    await seed_epochs(env.engine, {f"room:{ROOM_B}": 2})
    llm = FakeLLM(outputs={"context": CONTEXT_OK})

    await env.prepare(llm)

    assert _roles(llm) == Counter({"context": 1, "banter": 2})
    old, new = await _prep_rows(env.engine)
    assert new["dossier_id"] != old["dossier_id"]
    assert await _dossier_count(env.engine) == 2


# --- ⑩ SENTENCE 부분 일치 ----------------------------------------------------------------


async def test_10_SENTENCE_는_부분_일치_prep_의_조서를_쓰고_키_없는_강도는_후보_없이_서기(
    env: Env, snapshot_path: Path
):
    await env.prepare(FakeLLM(outputs={"context": CONTEXT_OK}))
    [prep] = await _prep_rows(env.engine)

    def mild_target(data: dict[str, Any]) -> None:
        _room_b_mild(data)
        data["jury"]["target_intensities"] = ["spicy", "mild"]

    _rewrite(snapshot_path, mild_target)
    env.fake.calls.clear()
    llm = PlannedLLM()
    capture = Capture()

    job_id = await env.sentence(llm, handler=SentenceHandler(capture))

    assert (await env.fetch_job(job_id))["status"] == "SUCCEEDED"
    state = capture.last
    assert state["dossier_source"] == "PREP"
    assert state["dossier"].dossier_id == prep["dossier_id"]
    assert "context" not in llm.roles()
    assert "resolve-evidence" not in env.paths()
    candidates = {
        _user(messages)["intensity"]: _user(messages)["banter_candidates"]
        for _, _, messages in llm.of("writer")
    }
    assert set(candidates) == {"spicy", "mild"}
    assert candidates["spicy"]  # 키가 맞는 강도는 후보를 쓴다
    assert candidates["mild"] == []  # 키 없는 강도는 후보 없이
    assert [req.dossier_id for req in env.backend.finalized] == [prep["dossier_id"]]
