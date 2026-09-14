"""관측 엔드포인트(08 §3.3, 10 §4.5 제안). 실제 Postgres.

① 두 엔드포인트 모두 토큰 없음·틀림 401 ② snapshot: 프로세스 레지스트리 + DB 지표, 각 `source`
③ trace: 최신 dossier 라벨·fact_type·scope, 출처 개수, llm_calls 노드 타임라인
④ 응답에 원문·개별 id 없음.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.api.app import create_app
from geoji_ai.core.config import Settings
from geoji_ai.telemetry.metrics import MetricsRegistry

TOKEN = "telemetry-test-token-41ab"
POST = "post-trace-1"
EVIDENCE_TEXT = "스타벅스 이번 달 세 번째 결제 원문"
NOW = datetime.now(UTC)


@pytest.fixture
def registry() -> MetricsRegistry:
    return MetricsRegistry()


@pytest.fixture
async def client(
    engine: AsyncEngine, registry: MetricsRegistry
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,
        SERVICE_AUTH_TOKEN=TOKEN,
        OPENAI_API_KEY="sk-test",
        XAI_API_KEY="xai-test",
    )
    app = create_app(settings)
    app.state.telemetry_engine = engine
    app.state.metrics_registry = registry
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ai") as c:
        yield c


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "path", ["/internal/v1/metrics/snapshot", f"/internal/v1/trials/{POST}/trace"]
)
async def test_인증이_없거나_틀리면_401(client: httpx.AsyncClient, path: str):
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers=auth("wrong"))).status_code == 401


async def _set_job_times(engine: AsyncEngine, job_id: str, status: str, wait_s: float) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ai.jobs SET status = :status, attempts = 1, owner_id = 'worker-a', "
                "generation_id = gen_random_uuid(), lease_until = now(), "
                "available_at = :avail, updated_at = :upd WHERE id = CAST(:id AS uuid)"
            ),
            {
                "status": status,
                "avail": NOW - timedelta(seconds=wait_s),
                "upd": NOW,
                "id": job_id,
            },
        )


async def _insert_call(
    engine: AsyncEngine,
    *,
    post_id: str,
    node: str,
    call_index: int,
    vendor: str,
    status: str,
    started_offset_s: float,
    duration_s: float | None,
    actual: int | None,
    estimated: int = 500,
    generation_id: str | None = None,
) -> str:
    call_id = str(uuid.uuid4())
    started = NOW - timedelta(seconds=started_offset_s)
    finished = None if duration_s is None else started + timedelta(seconds=duration_s)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ai.llm_calls (id, post_id, job_id, generation_id, node, call_index, "
                "vendor, model_id, request_hash, status, "
                "estimated_max_micro_usd, actual_micro_usd, "
                "prompt_tokens, completion_tokens, started_at, finished_at) VALUES "
                "(CAST(:id AS uuid), :post_id, NULL, CAST(:gen AS uuid), :node, :idx, :vendor, "
                "'model-x', 'req-hash', :status, :est, :actual, 100, 40, :started, :finished)"
            ),
            {
                "id": call_id,
                "post_id": post_id,
                "gen": generation_id or str(uuid.uuid4()),
                "node": node,
                "idx": call_index,
                "vendor": vendor,
                "status": status,
                "est": estimated,
                "actual": actual,
                "started": started,
                "finished": finished,
            },
        )
    return call_id


def _find(metrics: list[dict[str, Any]], name: str, source: str, **labels: str) -> dict[str, Any]:
    found = [
        m for m in metrics if m["name"] == name and m["source"] == source and m["labels"] == labels
    ]
    assert len(found) == 1, (name, source, labels, metrics)
    return found[0]


async def test_snapshot_은_프로세스와_DB_지표를_source_를_달아_합친다(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    registry: MetricsRegistry,
    enqueue: Callable[..., Awaitable[str]],
):
    # 대기 시간: SENTENCE 2건(10초·30초 대기 뒤 claim), 아직 대기 중인 PREPARE 1건(120초)
    s1 = await enqueue("SENTENCE", dedupe_key="s-1", verdict_id="v-a", verdict_version=1)
    s2 = await enqueue("SENTENCE", dedupe_key="s-2", verdict_id="v-b", verdict_version=1)
    await _set_job_times(engine, s1, "SUCCEEDED", 10)
    await _set_job_times(engine, s2, "RUNNING", 30)
    await enqueue(
        "PREPARE",
        dedupe_key="p-3",
        available_at=NOW - timedelta(seconds=120),
        post_id="p-3",
        post_version=1,
    )

    # llm_calls: writer/xai 2초·4초, UNKNOWN 1건, 사건 두 개
    await _insert_call(
        engine,
        post_id="p-1",
        node="writer",
        call_index=0,
        vendor="xai",
        status="COMPLETE",
        started_offset_s=60,
        duration_s=2,
        actual=300,
    )
    await _insert_call(
        engine,
        post_id="p-1",
        node="writer",
        call_index=1,
        vendor="xai",
        status="COMPLETE",
        started_offset_s=50,
        duration_s=4,
        actual=200,
    )
    await _insert_call(
        engine,
        post_id="p-2",
        node="writer",
        call_index=0,
        vendor="xai",
        status="UNKNOWN",
        started_offset_s=40,
        duration_s=None,
        actual=None,
    )
    await _insert_call(
        engine,
        post_id="p-2",
        node="evaluator",
        call_index=0,
        vendor="openai",
        status="COMPLETE",
        started_offset_s=30,
        duration_s=1,
        actual=50,
    )

    registry.histogram("first_result_latency_seconds", 5.0, path="guilty")
    registry.counter("lease_expired_total", kind="SENTENCE")

    response = await client.get("/internal/v1/metrics/snapshot", headers=auth())
    assert response.status_code == 200
    body = response.json()
    metrics = body["metrics"]
    assert "generated_at" in body

    assert _find(metrics, "first_result_latency_seconds", "process", path="guilty")["p95"] == 5.0
    assert _find(metrics, "lease_expired_total", "process", kind="SENTENCE")["value"] == 1

    wait = _find(metrics, "queue_wait_seconds", "db", kind="SENTENCE")
    assert wait["count"] == 2
    assert wait["p50"] == pytest.approx(10, abs=1)
    assert wait["p99"] == pytest.approx(30, abs=1)
    oldest = _find(metrics, "queue_oldest_age_seconds", "db", kind="PREPARE")
    assert oldest["value"] >= 119

    duration = _find(metrics, "llm_duration_seconds", "db", node="writer", vendor="xai")
    assert duration["count"] == 2
    assert (duration["p50"], duration["p99"]) == (pytest.approx(2.0), pytest.approx(4.0))

    cost_xai = _find(metrics, "case_cost_micro_usd", "db", vendor="xai")
    assert cost_xai["sum"] == 500 and cost_xai["count"] == 1  # p-1 한 사건만 실지출
    assert _find(metrics, "case_cost_micro_usd", "db", vendor="openai")["sum"] == 50
    assert _find(metrics, "unknown_calls", "db", vendor="xai")["value"] == 1

    for m in metrics:
        assert m["source"] in {"process", "db"}


async def test_snapshot_은_DB_가_비어도_모양을_지킨다(client: httpx.AsyncClient):
    response = await client.get("/internal/v1/metrics/snapshot", headers=auth())
    assert response.status_code == 200
    assert response.json()["metrics"] == []


async def _seed_trace(engine: AsyncEngine) -> dict[str, str]:
    old_dossier = str(uuid.uuid4())
    dossier = str(uuid.uuid4())
    ev1, ev2 = str(uuid.uuid4()), str(uuid.uuid4())
    memory_source = f"memfact-{uuid.uuid4()}"
    async with engine.begin() as conn:
        for did, created in ((old_dossier, NOW - timedelta(hours=1)), (dossier, NOW)):
            await conn.execute(
                text(
                    "INSERT INTO ai.dossiers "
                    "(id, post_id, snapshot_hash, label_map, privacy_versions, created_at) "
                    "VALUES (CAST(:id AS uuid), :post, 'snap-hash', CAST(:lm AS jsonb), "
                    "'[]'::jsonb, :created)"
                ),
                {
                    "id": did,
                    "post": POST,
                    "lm": json.dumps({"E1": ev1, "E2": ev2}),
                    "created": created,
                },
            )
        for eid, label, ep, ft, scope in (
            (ev1, "E1", "DB_RECORD", "SPEND", {"visibility": "PUBLIC", "room_ids": []}),
            (
                ev2,
                "E2",
                "DB_RECORD",
                "AGGREGATE",
                {"visibility": "ROOM", "room_ids": ["room-77", "room-78"]},
            ),
        ):
            await conn.execute(
                text(
                    "INSERT INTO ai.evidence "
                    "(id, dossier_id, label, epistemic_type, fact_type, text, scope) "
                    "VALUES (CAST(:id AS uuid), CAST(:d AS uuid), :label, :ep, :ft, :text, "
                    "CAST(:scope AS jsonb))"
                ),
                {
                    "id": eid,
                    "d": dossier,
                    "label": label,
                    "ep": ep,
                    "ft": ft,
                    "text": EVIDENCE_TEXT,
                    "scope": json.dumps(scope),
                },
            )
        for eid, stype, sid, ver in (
            (ev1, "post", "post-src-9001", 3),
            (ev2, "memory_fact", memory_source, 1),
            (ev2, "memory_fact", f"memfact-{uuid.uuid4()}", 1),
            (ev2, "post", "post-src-9002", 2),
        ):
            await conn.execute(
                text(
                    "INSERT INTO ai.evidence_sources "
                    "(evidence_id, source_type, source_id, source_version) "
                    "VALUES (CAST(:e AS uuid), :t, :s, :v)"
                ),
                {"e": eid, "t": stype, "s": sid, "v": ver},
            )
    gen = str(uuid.uuid4())
    await _insert_call(
        engine,
        post_id=POST,
        node="sentencing",
        call_index=0,
        vendor="openai",
        status="COMPLETE",
        started_offset_s=20,
        duration_s=3,
        actual=120,
        generation_id=gen,
    )
    await _insert_call(
        engine,
        post_id=POST,
        node="writer",
        call_index=0,
        vendor="xai",
        status="UNKNOWN",
        started_offset_s=10,
        duration_s=None,
        actual=None,
        estimated=900,
        generation_id=gen,
    )
    await _insert_call(
        engine,
        post_id="other-post",
        node="writer",
        call_index=0,
        vendor="xai",
        status="COMPLETE",
        started_offset_s=5,
        duration_s=1,
        actual=1,
    )
    return {
        "dossier": dossier,
        "old": old_dossier,
        "ev1": ev1,
        "memory_source": memory_source,
        "gen": gen,
    }


async def test_trace_는_최신_dossier_라벨과_출처_개수와_노드_타임라인을_낸다(
    client: httpx.AsyncClient, engine: AsyncEngine
):
    ids = await _seed_trace(engine)

    response = await client.get(f"/internal/v1/trials/{POST}/trace", headers=auth())
    assert response.status_code == 200
    body = response.json()

    assert body["post_id"] == POST
    dossier = body["dossier"]
    assert dossier["labels"] == ["E1", "E2"]
    assert dossier["invalidated"] is False
    assert dossier["created_at"]
    evidence = dossier["evidence"]
    assert [e["label"] for e in evidence] == ["E1", "E2"]
    e1, e2 = evidence
    assert (e1["epistemic_type"], e1["fact_type"]) == ("DB_RECORD", "SPEND")
    assert e1["scope"] == {"visibility": "PUBLIC", "room_count": 0}
    assert e2["fact_type"] == "AGGREGATE"
    assert e2["scope"] == {"visibility": "ROOM", "room_count": 2}
    assert e2["sources"] == [
        {"source_type": "memory_fact", "count": 2},
        {"source_type": "post", "count": 1},
    ]
    assert e1["sources"] == [{"source_type": "post", "count": 1}]

    timeline = body["timeline"]
    assert [(t["node"], t["vendor"], t["status"]) for t in timeline] == [
        ("sentencing", "openai", "COMPLETE"),
        ("writer", "xai", "UNKNOWN"),
    ]
    assert timeline[0]["duration_ms"] == 3000
    assert timeline[0]["prompt_tokens"] == 100
    assert timeline[1]["duration_ms"] is None
    assert body["cost"] == {
        "actual_micro_usd": 120,
        "unknown_calls": 1,
        "unknown_estimated_max_micro_usd": 900,
        "calls": 2,
    }

    raw = response.text
    assert EVIDENCE_TEXT not in raw
    for secret in (
        ids["dossier"],
        ids["old"],
        ids["ev1"],
        ids["memory_source"],
        ids["gen"],
        "post-src-9001",
        "room-77",
        "snap-hash",
        "req-hash",
    ):
        assert secret not in raw, secret
    assert '"text"' not in raw


async def test_trace_는_기록이_없으면_404(client: httpx.AsyncClient):
    response = await client.get("/internal/v1/trials/no-such-post/trace", headers=auth())
    assert response.status_code == 404
    assert response.json() == {"code": "TRACE_NOT_FOUND"}
