"""데모 C 시드 → 근거 pack(04 §3.6·§4.1 데모 C·§4.2 통합, ME-07).

실제 Postgres + 가짜 백엔드(`resolve_fixture`·`snapshot_fixture`).

① 시드 2회 → `memory_facts` 행 수가 1회와 같다(멱등)
② 3번째 사건 `created_at` 을 before 로 `recall_user` → 시드 post 2건의 참조(payload 없음)
③ 가짜 백엔드 `BackendHttp.resolve_evidence` → sources 2
④ `build_evidence` → F0 THIS_CASE + AGGREGATE 2(반복 "2건") + PRIOR 2 (+ MEM 2) 스냅샷
⑤ `PostgresPreparation.save_dossier` 저장 성공
⑥ `target_pack(dossier, [다른 방])` 에 `ROOMS [R]` 근거 없음
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.backend_http import BackendHttp
from geoji_ai.adapters.postgres_memory import PostgresMemory
from geoji_ai.adapters.postgres_preparation import PostgresPreparation
from geoji_ai.application.build_evidence import build_evidence, target_pack
from geoji_ai.contracts.case import CaseSnapshot
from geoji_ai.domain.visibility import Visibility
from geoji_ai.ports.backend import (
    EvidenceCandidate,
    ResolveEvidenceRequest,
    ResolveEvidenceResponse,
)
from geoji_ai.ports.memory import MemoryCandidate
from geoji_ai.ports.preparation import Dossier
from tests.fakes.backend_app import FAKE_SERVICE_TOKEN, create_fake_backend
from tests.integration.test_retain_handler import seed_epochs
from tests.integration.test_worker_runtime import make_settings

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "contracts" / "fixtures" / "case-snapshot-starbucks-3rd.json"
RESOLVE_PATH = ROOT / "tests" / "fakes" / "resolve_starbucks.json"
SCRIPT_PATH = ROOT / "scripts" / "seed_memory_demo_c.py"

# fixture 두 파일과 같은 임의 식별자.
USER = "user-demo-c"
ROOM = "room-demo-c"
OTHER_ROOM = "room-demo-c-other"
POST_1 = "post-starbucks-1"
POST_2 = "post-starbucks-2"
POST_3 = "post-starbucks-3rd"
CATEGORY = "카페/간식"

JOB_ID = "job-demo-c"
GENERATION_ID = "gen-demo-c"
VERDICT_3 = "verdict-demo-c-3rd"
PACK_LIMIT: int = make_settings().EVIDENCE_PACK_LIMIT


def _load_script() -> ModuleType:
    name = "seed_memory_demo_c"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seed_script = _load_script()


def _fixture_snapshot() -> CaseSnapshot:
    return CaseSnapshot.model_validate(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))


async def _seed(engine: AsyncEngine) -> list[tuple[str, int]]:
    return await seed_script.seed(
        engine,
        make_settings(),
        user=USER,
        room=ROOM,
        post_ids=[POST_1, POST_2],
        now=_fixture_snapshot().created_at,
    )


async def _fact_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM ai.memory_facts"))).scalar_one())


@pytest.fixture
async def backend() -> AsyncIterator[BackendHttp]:
    """데모 C fixture 를 돌려주는 가짜 백엔드. job 은 `ai.jobs` 없이 앱 상태에 심는다."""
    app = create_fake_backend(snapshot_fixture=SNAPSHOT_PATH, resolve_fixture=RESOLVE_PATH)
    fake = app.state.fake
    fake.seed_verdict(VERDICT_3, post_id=POST_3, deadline_at=None)
    fake.jobs[JOB_ID] = {"generation_id": GENERATION_ID, "verdict_id": VERDICT_3}
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    http = BackendHttp("http://fake-backend", FAKE_SERVICE_TOKEN, client=client)
    try:
        yield http
    finally:
        await http.aclose()


@dataclasses.dataclass(frozen=True)
class _Pack:
    snapshot: CaseSnapshot
    candidates: list[MemoryCandidate]
    resolved: ResolveEvidenceResponse
    dossier: Dossier


@pytest.fixture
async def demo_c(engine: AsyncEngine, privacy_epochs: str, backend: BackendHttp) -> _Pack:
    """시드 → snapshot → recall → resolve-evidence → build_evidence."""
    snapshot = await backend.snapshot(JOB_ID, GENERATION_ID)
    await seed_epochs(engine, {pv.scope_key: pv.epoch for pv in snapshot.privacy_versions})
    await _seed(engine)
    candidates = await PostgresMemory(engine, make_settings()).recall_user(
        snapshot.author_id, snapshot.category, snapshot.created_at, reason=snapshot.reason
    )
    request = ResolveEvidenceRequest(
        candidates=[
            EvidenceCandidate(
                source_type=c.source_type,
                source_id=c.source_id,
                source_version=c.source_version,
                score=c.score,
            )
            for c in candidates
        ],
        include=["rules", "aggregates", "recent_verdicts"],
    )
    resolved = await backend.resolve_evidence(JOB_ID, GENERATION_ID, request)
    dossier = build_evidence(snapshot, resolved, pack_limit=PACK_LIMIT)
    return _Pack(snapshot, candidates, resolved, dossier)


# --- ① -------------------------------------------------------------------------


async def test_01_시드_2회는_행_수가_1회와_같다(engine: AsyncEngine):
    first = await _seed(engine)
    after_first = await _fact_count(engine)
    second = await _seed(engine)

    # 사용자 SPEND·VERDICT 2건씩 + 방 VERDICT 2건
    assert after_first == 6
    assert [rows for _, rows in first] == [3, 3]
    assert [rows for _, rows in second] == [0, 0]
    assert await _fact_count(engine) == after_first


async def test_01_시드_행은_04_3_6_값이다(engine: AsyncEngine):
    now = _fixture_snapshot().created_at
    await _seed(engine)

    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT bank_type, bank_id, fact_type, source_type, source_id, payload, "
                        "scope, occurred_at FROM ai.memory_facts ORDER BY occurred_at, bank_type, "
                        "fact_type"
                    )
                )
            )
            .mappings()
            .all()
        )
    user_spend = [r for r in rows if r["bank_type"] == "user" and r["fact_type"] == "SPEND"]
    user_verdict = [r for r in rows if r["bank_type"] == "user" and r["fact_type"] == "VERDICT"]
    room_verdict = [r for r in rows if r["bank_type"] == "room"]

    assert [(r["source_type"], r["source_id"]) for r in user_spend] == [
        ("POST", POST_1),
        ("POST", POST_2),
    ]
    assert [r["occurred_at"] for r in user_spend] == [
        now - seed_script.DEMO_SEEDS[0][0],
        now - seed_script.DEMO_SEEDS[1][0],
    ]
    for r in user_spend:
        assert r["bank_id"] == USER
        assert r["payload"]["category"] == CATEGORY
        assert r["payload"]["amount_krw"] == 6100
    assert [r["payload"]["guilty_ratio"] for r in user_verdict] == [0.8, 1.0]
    assert [r["payload"]["result"] for r in user_verdict + room_verdict] == ["guilty"] * 4
    assert {r["bank_id"] for r in room_verdict} == {ROOM}
    assert all(r["scope"] == {"visibility": "ROOMS", "room_ids": [ROOM]} for r in rows)


async def test_01_verdict_ids_를_주면_VERDICT_source_id_로_쓴다(engine: AsyncEngine):
    verdict_ids = ["verdict-starbucks-1", "verdict-starbucks-2"]
    await seed_script.seed(
        engine,
        make_settings(),
        user=USER,
        room=ROOM,
        post_ids=[POST_1, POST_2],
        now=_fixture_snapshot().created_at,
        verdict_ids=verdict_ids,
    )

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT source_id FROM ai.memory_facts "
                    "WHERE fact_type = 'VERDICT' AND bank_type = 'user' ORDER BY occurred_at"
                )
            )
        ).all()
    assert [r[0] for r in rows] == verdict_ids


# --- ② -------------------------------------------------------------------------


async def test_02_recall_user_는_시드_2건의_참조만(demo_c: _Pack):
    by_type: dict[str, list[MemoryCandidate]] = {}
    for candidate in demo_c.candidates:
        by_type.setdefault(candidate.fact_type, []).append(candidate)
        assert not hasattr(candidate, "payload")

    assert set(by_type) == {"SPEND", "VERDICT"}
    assert {c.source_id for c in by_type["SPEND"]} == {POST_1, POST_2}
    assert {c.source_id for c in by_type["VERDICT"]} == {
        seed_script.demo_verdict_id(POST_1),
        seed_script.demo_verdict_id(POST_2),
    }
    assert len(by_type["SPEND"]) == len(by_type["VERDICT"]) == 2


async def test_02_before_이후_사실은_recall_하지_않는다(engine: AsyncEngine):
    await _seed(engine)
    memory = PostgresMemory(engine, make_settings())
    snapshot = _fixture_snapshot()
    # 두 번째 시드(−4일) 바로 앞을 before 로 두면 첫 시드만 남는다.
    before: datetime = snapshot.created_at - seed_script.DEMO_SEEDS[1][0]

    found = await memory.recall_user(USER, CATEGORY, before)

    assert {c.source_id for c in found if c.fact_type == "SPEND"} == {POST_1}


# --- ③ -------------------------------------------------------------------------


async def test_03_resolve_evidence_는_sources_2(demo_c: _Pack):
    resolved = demo_c.resolved

    assert [(s.source_type, s.source_id) for s in resolved.sources] == [
        ("POST", POST_1),
        ("POST", POST_2),
    ]
    for source in resolved.sources:
        assert set(source.payload) >= {"category", "amount_krw", "reason", "spent_at"}
        assert (source.scope.visibility, source.scope.room_ids) == ("ROOMS", [ROOM])
    assert resolved.aggregates.repeat_same_category_30d == 2
    assert resolved.aggregates.excludes_post_id == POST_3
    assert [v.result for v in resolved.recent_verdicts] == ["guilty", "guilty"]
    assert len(resolved.room_rules) == 1
    assert resolved.style_comments == []


# --- ④ -------------------------------------------------------------------------

_ROOMS_R = (Visibility.ROOMS, frozenset({ROOM}))

#: (label, fact_type, epistemic_type, (visibility, room_ids)). 문장 본문은 비교하지 않는다.
EXPECTED_PACK: list[tuple[str, str, str, tuple[Visibility, frozenset[str]]]] = [
    ("F0", "SPEND", "USER_CLAIM", _ROOMS_R),  # THIS_CASE
    ("F1", "AGGREGATE", "DB_RECORD", _ROOMS_R),  # 소진율·티어·무지출
    ("F2", "AGGREGATE", "DB_RECORD", _ROOMS_R),  # 반복
    ("F3", "VERDICT", "DB_RECORD", _ROOMS_R),  # PRIOR (최신 먼저)
    ("F4", "VERDICT", "DB_RECORD", _ROOMS_R),  # PRIOR
    ("F5", "SPEND", "DB_RECORD", _ROOMS_R),  # MEM (sources)
    ("F6", "SPEND", "DB_RECORD", _ROOMS_R),  # MEM
]


def _shape(dossier: Dossier) -> list[tuple[str, str, str, tuple[Visibility, frozenset[str]]]]:
    return [
        (f.label, f.fact_type, f.epistemic_type, (f.scope.visibility, f.scope.room_ids))
        for f in dossier.facts
    ]


async def test_04_build_evidence_pack_스냅샷(demo_c: _Pack):
    dossier = demo_c.dossier

    assert _shape(dossier) == EXPECTED_PACK
    facts = {f.label: f for f in dossier.facts}
    assert facts["F0"].sources == (("POST", POST_3, 1),)
    assert "2건" in facts["F2"].text
    for label in ("F1", "F2"):
        aggregation: dict[str, Any] | None = facts[label].aggregation
        assert aggregation is not None
        assert aggregation["excludes_post_id"] == POST_3
    assert [facts[label].sources for label in ("F3", "F4")] == [
        (("POST", POST_2, 1),),
        (("POST", POST_1, 1),),
    ]
    assert sorted(dossier.label_map) == [label for label, *_ in EXPECTED_PACK]


# --- ⑤ -------------------------------------------------------------------------


async def test_05_save_dossier_저장(engine: AsyncEngine, demo_c: _Pack):
    saved = await PostgresPreparation(engine).save_dossier(demo_c.dossier)

    assert saved == demo_c.dossier.dossier_id
    async with engine.connect() as conn:
        post_id = (
            await conn.execute(
                text("SELECT post_id FROM ai.dossiers WHERE id = CAST(:id AS uuid)"), {"id": saved}
            )
        ).scalar_one()
        evidence = int((await conn.execute(text("SELECT count(*) FROM ai.evidence"))).scalar_one())
    assert post_id == POST_3
    assert evidence == len(EXPECTED_PACK)


# --- ⑥ -------------------------------------------------------------------------


async def test_06_다른_방_pack_에_ROOMS_R_근거_없음(demo_c: _Pack):
    other = target_pack(demo_c.dossier, [OTHER_ROOM])

    assert not any(
        f.scope.visibility is Visibility.ROOMS and ROOM in f.scope.room_ids for f in other
    )
    # 대조: 그 방 자신에게는 전부 쓸 수 있다.
    assert len(target_pack(demo_c.dossier, [ROOM])) == len(EXPECTED_PACK)
