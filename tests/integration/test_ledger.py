"""예산·원장·node_results(06 §3.2·§4.2). 실제 Postgres."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.adapters.postgres_jobs import make_engine
from geoji_ai.domain.budget import (
    CASE_CAP_MICRO_USD,
    NodeResultKey,
    budget_key_for_post,
    budget_key_for_submission,
    request_hash,
)
from geoji_ai.ports.ledger import BudgetExceeded, CallSpec, LedgerPort
from geoji_ai.ports.llm import Cost, LLMError, LLMResult, Usage

POST = budget_key_for_post("post-1")


def _role_url(url: str, role: str) -> str:
    return make_url(url).set(username=role, password=role).render_as_string(hide_password=False)


def _spec(
    est: int,
    *,
    node: str = "writer",
    call_index: int = 0,
    generation_id: str | None = None,
    vendor: str = "xai",
    model: str = "grok-test",
) -> CallSpec:
    return CallSpec(
        node=node,
        call_index=call_index,
        vendor=vendor,
        model=model,
        est_max_micro_usd=est,
        request_hash=request_hash({"node": node, "i": call_index}),
        generation_id=generation_id,
        job_id=str(uuid.uuid4()),
    )


def _result(
    *,
    micro_usd: int | None = None,
    ticks: int | None = None,
    usage: Usage | None = None,
) -> LLMResult:
    source = "unknown" if micro_usd is None and ticks is None else "usage"
    return LLMResult(
        output={"ok": True},
        stop_reason="stop",
        usage=usage or Usage(prompt_tokens=120, completion_tokens=80, reasoning_tokens=5),
        cost=Cost(ticks=ticks, micro_usd=micro_usd, source=source),
        provider_request_id="req-1",
        model_id="grok-test",
        vendor="xai",
        latency_ms=900,
    )


async def _budget(engine: AsyncEngine, key: str = POST) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM ai.case_budgets WHERE post_id = :k"), {"k": key}
                )
            )
            .mappings()
            .first()
        )
    return None if row is None else dict(row)


async def _call(engine: AsyncEngine, call_id: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM ai.llm_calls WHERE id = CAST(:id AS uuid)"),
                    {"id": call_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def _call_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM ai.llm_calls"))).scalar_one())


def test_어댑터는_포트를_구현한다(engine: AsyncEngine):
    assert isinstance(PostgresCallLedger(engine), LedgerPort)


# ① reserve → settle
async def test_1_reserve_settle_뒤_reserved_0_spent_actual(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(5_000))

    budget = await _budget(engine)
    assert budget == {
        "post_id": POST,
        "cap_micro_usd": CASE_CAP_MICRO_USD,
        "spent_micro_usd": 0,
        "reserved_micro_usd": 5_000,
    }
    row = await _call(engine, call_id)
    assert row["status"] == "RESERVED"
    assert row["estimated_max_micro_usd"] == 5_000
    assert row["started_at"] is not None

    await ledger.settle(call_id, _result(micro_usd=1_234))

    budget = await _budget(engine)
    assert budget["reserved_micro_usd"] == 0
    assert budget["spent_micro_usd"] == 1_234
    row = await _call(engine, call_id)
    assert row["status"] == "COMPLETE"
    assert row["actual_micro_usd"] == 1_234
    assert (row["prompt_tokens"], row["completion_tokens"], row["reasoning_tokens"]) == (120, 80, 5)
    assert row["provider_request_id"] == "req-1"
    assert row["finished_at"] is not None


async def test_1b_settle_두_번은_이중_정산하지_않는다(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(5_000))
    await ledger.settle(call_id, _result(micro_usd=1_000))
    await ledger.settle(call_id, _result(micro_usd=1_000))
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (1_000, 0)


async def test_1c_비용_모르면_est_max_를_spent_에_actual_은_null(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(3_000))
    await ledger.settle(call_id, _result())
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (3_000, 0)
    row = await _call(engine, call_id)
    assert row["actual_micro_usd"] is None
    assert row["cost_ticks"] is None


# ② cap 초과
async def test_2_cap_초과는_budget_exceeded_llm_calls_0행_reserved_불변(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine, cap_micro_usd=10_000)
    await ledger.reserve(POST, _spec(6_000, call_index=0))
    before = await _budget(engine)

    with pytest.raises(BudgetExceeded) as caught:
        await ledger.reserve(POST, _spec(4_001, call_index=1))

    assert caught.value.cap_micro_usd == 10_000
    assert await _budget(engine) == before
    assert await _call_count(engine) == 1


async def test_2b_첫_예약부터_cap_초과면_행을_남기지_않는다(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine, cap_micro_usd=100)
    with pytest.raises(BudgetExceeded):
        await ledger.reserve(POST, _spec(101))
    assert await _budget(engine) is None
    assert await _call_count(engine) == 0


async def test_2c_cap_과_같으면_예약된다(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine, cap_micro_usd=100)
    await ledger.reserve(POST, _spec(100))
    assert (await _budget(engine))["reserved_micro_usd"] == 100


# ③ 동시 reserve
async def test_3_동시_reserve_둘이_cap_을_같이_넘지_못한다(engine: AsyncEngine):
    # 빈 DB 에서 동시에 보내면 `INSERT ... ON CONFLICT` 가 같은 키를 기다려 잠금 없이도 차례로
    # 돈다. `case_budgets` 행을 먼저 커밋해 두어야 `FOR UPDATE` 가 빠진 회귀를 잡는다.
    ledger = PostgresCallLedger(engine, cap_micro_usd=10_000)
    rounds = 10
    for i in range(rounds):
        key = f"{POST}-r{i}"
        await ledger.reserve(key, _spec(1, call_index=9))
        outcomes = await asyncio.gather(
            ledger.reserve(key, _spec(6_000, call_index=0)),
            ledger.reserve(key, _spec(6_000, call_index=1)),
            return_exceptions=True,
        )
        errors = [o for o in outcomes if isinstance(o, BaseException)]
        assert len(errors) == 1, (i, outcomes)
        assert isinstance(errors[0], BudgetExceeded)
        assert (await _budget(engine, key))["reserved_micro_usd"] == 6_001
    assert await _call_count(engine) == rounds * 2


# ④ mark_unknown
async def test_4_mark_unknown_은_예약_유지_spent_불변(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(5_000))
    await ledger.mark_unknown(call_id, LLMError("TIMEOUT"))

    row = await _call(engine, call_id)
    assert row["status"] == "UNKNOWN"
    assert row["actual_micro_usd"] is None
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (0, 5_000)

    # UNKNOWN 행에 뒤늦은 settle 이 와도 정산하지 않는다
    await ledger.settle(call_id, _result(micro_usd=1))
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (0, 5_000)


# ⑤ fail + usage
async def test_5_fail_usage_는_spent_반영_reserved_해제(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(5_000))
    error = LLMError(
        "PARSE",
        usage=Usage(prompt_tokens=100, completion_tokens=900),
        cost=Cost(ticks=21_000_000, micro_usd=None, source="usage"),
    )
    await ledger.fail(call_id, error)

    row = await _call(engine, call_id)
    assert row["status"] == "FAILED"
    assert row["actual_micro_usd"] == 2_100
    assert row["cost_ticks"] == 21_000_000
    assert (row["prompt_tokens"], row["completion_tokens"]) == (100, 900)
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (2_100, 0)


async def test_5b_fail_응답_없음은_spent_0_reserved_해제(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(5_000))
    await ledger.fail(call_id, LLMError("RATE_LIMIT"))
    assert (await _call(engine, call_id))["status"] == "FAILED"
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (0, 0)


async def test_5c_fail_usage_만_있고_비용_모르면_est_max(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(5_000))
    await ledger.fail(call_id, LLMError("SCHEMA", usage=Usage(prompt_tokens=10)))
    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (5_000, 0)


# ⑥ UNIQUE(generation_id, node, call_index)
async def test_6_같은_generation_node_call_index_중복_reserve_거부(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    generation_id = str(uuid.uuid4())
    await ledger.reserve(POST, _spec(1_000, generation_id=generation_id, call_index=2))

    with pytest.raises(IntegrityError):
        await ledger.reserve(POST, _spec(2_000, generation_id=generation_id, call_index=2))

    assert await _call_count(engine) == 1
    assert (await _budget(engine))["reserved_micro_usd"] == 1_000

    # 다른 슬롯은 된다
    await ledger.reserve(POST, _spec(2_000, generation_id=generation_id, call_index=3))
    assert (await _budget(engine))["reserved_micro_usd"] == 3_000


# ⑦ xAI ticks
async def test_7_ticks_는_micro_usd_내림_cost_ticks_원값(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(POST, _spec(20_000))
    await ledger.settle(call_id, _result(ticks=123_456_789))

    row = await _call(engine, call_id)
    assert row["actual_micro_usd"] == 12_345
    assert row["cost_ticks"] == 123_456_789
    assert (await _budget(engine))["spent_micro_usd"] == 12_345


async def test_7b_제출_예산_키도_같은_원장을_쓴다(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    key = budget_key_for_submission("s-1")
    call_id = await ledger.reserve(key, _spec(700, node="intake"))
    assert (await _call(engine, call_id))["post_id"] == "submission:s-1"
    assert (await _budget(engine, key))["reserved_micro_usd"] == 700
    assert await _budget(engine) is None


# ⑧ node_results
_KEY = NodeResultKey(
    request_hash=request_hash({"case": "택시", "intensity": "hell"}),
    model_id="grok-test",
    prompt_version="bundle-v2+abcd1234",
    policy_version="guardrail-v2",
    privacy_versions=[{"scope_key": "user:u1", "epoch": 3}],
)
_OUTPUT = {"headline": "지갑 사망 선고", "statement": ["택시 또 탔네"]}


async def _stored(ledger: PostgresCallLedger, **put_kwargs: Any) -> str:
    call_id = await ledger.reserve(POST, _spec(1_000))
    await ledger.put_node_result(call_id, _KEY.request_hash, _KEY.versions(), _OUTPUT, **put_kwargs)
    return call_id


async def test_8_node_results_5요소_같으면_hit(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    await _stored(ledger)
    assert await ledger.get_node_result(_KEY.request_hash, _KEY.versions()) == _OUTPUT


@pytest.mark.parametrize(
    "field, value",
    [
        ("model_id", "gpt-5.6-luna"),
        ("prompt_version", "bundle-v2+ffff0000"),
        ("policy_version", "guardrail-v1"),
        ("privacy_versions", [{"scope_key": "user:u1", "epoch": 4}]),
    ],
)
async def test_8_node_results_한_요소만_달라도_miss(engine: AsyncEngine, field: str, value: Any):
    ledger = PostgresCallLedger(engine)
    await _stored(ledger)
    versions = {**_KEY.versions(), field: value}
    assert await ledger.get_node_result(_KEY.request_hash, versions) is None


async def test_8_node_results_request_hash_가_다르면_miss(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    await _stored(ledger)
    other = request_hash({"case": "택시", "intensity": "mild"})
    assert await ledger.get_node_result(other, _KEY.versions()) is None


async def test_8_node_results_만료면_miss(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    await _stored(ledger, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert await ledger.get_node_result(_KEY.request_hash, _KEY.versions()) is None


async def test_8_node_results_clock_기준_만료(engine: AsyncEngine):
    now = datetime.now(UTC)
    writer = PostgresCallLedger(engine, clock=lambda: now)
    await _stored(writer)
    later = PostgresCallLedger(engine, clock=lambda: now + timedelta(hours=24, seconds=1))
    assert await writer.get_node_result(_KEY.request_hash, _KEY.versions()) == _OUTPUT
    assert await later.get_node_result(_KEY.request_hash, _KEY.versions()) is None


async def test_8_node_results_invalidated_면_miss(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await _stored(ledger)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE ai.node_results SET invalidated_at = now() WHERE call_id = :id"),
            {"id": uuid.UUID(call_id)},
        )
    assert await ledger.get_node_result(_KEY.request_hash, _KEY.versions()) is None


async def test_8_node_results_expires_at_기본은_24h(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await _stored(ledger)
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT expires_at - created_at AS ttl FROM ai.node_results "
                        "WHERE call_id = :id"
                    ),
                    {"id": uuid.UUID(call_id)},
                )
            )
            .mappings()
            .one()
        )
    assert row["ttl"] == timedelta(hours=24)


async def test_8_node_results_versions_요소가_빠지면_거부(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    versions = _KEY.versions()
    del versions["policy_version"]
    with pytest.raises(ValueError):
        await ledger.get_node_result(_KEY.request_hash, versions)
    with pytest.raises(ValueError):
        await ledger.put_node_result(
            "00000000-0000-0000-0000-000000000000", _KEY.request_hash, versions, {"ok": True}
        )


# ⑨ ai_worker role
async def test_9_ai_worker_role_로_전_과정_권한_오류_없음(
    engine: AsyncEngine, test_database_url: str
):
    worker_engine = make_engine(_role_url(test_database_url, "ai_worker"))
    try:
        async with worker_engine.connect() as conn:
            assert (await conn.execute(text("SELECT current_user"))).scalar_one() == "ai_worker"
        ledger = PostgresCallLedger(worker_engine, cap_micro_usd=10_000)
        generation_id = str(uuid.uuid4())

        settled = await ledger.reserve(POST, _spec(3_000, generation_id=generation_id))
        await ledger.settle(settled, _result(ticks=20_000_000))
        failed = await ledger.reserve(POST, _spec(1_000, generation_id=generation_id, call_index=1))
        await ledger.fail(failed, LLMError("PARSE", usage=Usage(1, 1), cost=Cost(micro_usd=10)))
        unknown = await ledger.reserve(
            POST, _spec(1_000, generation_id=generation_id, call_index=2)
        )
        await ledger.mark_unknown(unknown, LLMError("TRANSPORT"))
        with pytest.raises(BudgetExceeded):
            await ledger.reserve(POST, _spec(9_000, generation_id=generation_id, call_index=3))

        await ledger.put_node_result(settled, _KEY.request_hash, _KEY.versions(), _OUTPUT)
        assert await ledger.get_node_result(_KEY.request_hash, _KEY.versions()) == _OUTPUT
    finally:
        await worker_engine.dispose()

    budget = await _budget(engine)
    assert (budget["spent_micro_usd"], budget["reserved_micro_usd"]) == (2_000 + 10, 1_000)
