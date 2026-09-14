"""UNKNOWN 정리(08 §3.2·§4.2 `test_ledger_sweep`). 실제 Postgres.

24h 이전 UNKNOWN 만 정리 · 최근 UNKNOWN 유지 · COMPLETE(SUCCEEDED) 불변 · 재실행 멱등 ·
reserved 누수 0.
표시 규칙: `UNKNOWN ∧ actual_micro_usd NOT NULL` = 정리됨(코디네이터 9/14 Q1=a).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.domain.budget import budget_key_for_post, request_hash
from geoji_ai.ports.ledger import CallSpec, LedgerPort, SweepReport
from geoji_ai.ports.llm import Cost, LLMError, LLMResult, Usage

KEY_A = budget_key_for_post("post-sweep-a")
KEY_B = budget_key_for_post("post-sweep-b")
OLDER_THAN = timedelta(hours=24)


def _spec(est: int, index: int) -> CallSpec:
    return CallSpec(
        node="writer",
        call_index=index,
        vendor="xai",
        model="grok-test",
        est_max_micro_usd=est,
        request_hash=request_hash({"i": index}),
        generation_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
    )


def _result(micro_usd: int) -> LLMResult:
    return LLMResult(
        output={"ok": True},
        stop_reason="stop",
        usage=Usage(prompt_tokens=10, completion_tokens=10),
        cost=Cost(ticks=None, micro_usd=micro_usd, source="usage"),
        provider_request_id="req-1",
        model_id="grok-test",
        vendor="xai",
        latency_ms=100,
    )


async def _age(engine: AsyncEngine, call_id: str, hours: float) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ai.llm_calls SET started_at = now() - make_interval(secs => :s) "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"s": hours * 3600, "id": call_id},
        )


async def _call(engine: AsyncEngine, call_id: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM ai.llm_calls WHERE id = CAST(:id AS uuid)"), {"id": call_id}
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def _budget(engine: AsyncEngine, key: str) -> tuple[int, int]:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT spent_micro_usd, reserved_micro_usd FROM ai.case_budgets "
                        "WHERE post_id = :k"
                    ),
                    {"k": key},
                )
            )
            .mappings()
            .one()
        )
    return int(row["spent_micro_usd"]), int(row["reserved_micro_usd"])


async def _open_est(engine: AsyncEngine, key: str) -> int:
    """아직 예약이 걸린 호출(RESERVED·SENT·미정리 UNKNOWN)의 est 합."""
    async with engine.connect() as conn:
        value = (
            await conn.execute(
                text(
                    "SELECT COALESCE(SUM(estimated_max_micro_usd), 0) FROM ai.llm_calls "
                    "WHERE post_id = :k AND (status IN ('RESERVED','SENT') "
                    "OR (status = 'UNKNOWN' AND actual_micro_usd IS NULL))"
                ),
                {"k": key},
            )
        ).scalar_one()
    return int(value)


async def _insert_orphan_unknown(engine: AsyncEngine, est: int, hours: float) -> str:
    """`post_id` NULL 인 UNKNOWN 행(예산 키 없음)."""
    call_id = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ai.llm_calls (id, post_id, node, call_index, vendor, model_id, "
                "request_hash, status, estimated_max_micro_usd, started_at) VALUES ("
                "CAST(:id AS uuid), NULL, 'intake', 0, 'openai', 'gpt-test', 'h', 'UNKNOWN', :est, "
                "now() - make_interval(secs => :s))"
            ),
            {"id": call_id, "est": est, "s": hours * 3600},
        )
    return call_id


async def test_포트_시그니처에_sweep_unknown_이_있다(engine: AsyncEngine):
    assert isinstance(PostgresCallLedger(engine), LedgerPort)


async def test_24h_이전_UNKNOWN_만_spent_로_확정하고_재실행은_0건이다(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)

    old_a = await ledger.reserve(KEY_A, _spec(3_000, 0))
    await ledger.mark_unknown(old_a, LLMError("TIMEOUT"))
    await _age(engine, old_a, 25)

    recent_a = await ledger.reserve(KEY_A, _spec(2_000, 1))
    await ledger.mark_unknown(recent_a, LLMError("TRANSPORT"))
    await _age(engine, recent_a, 23)

    done_a = await ledger.reserve(KEY_A, _spec(1_000, 2))
    await ledger.settle(done_a, _result(400))
    await _age(engine, done_a, 30)
    done_before = await _call(engine, done_a)

    open_a = await ledger.reserve(KEY_A, _spec(700, 3))
    await _age(engine, open_a, 1)  # 최근 RESERVED 는 진행 중일 수 있어 유지

    old_b = await ledger.reserve(KEY_B, _spec(4_000, 4))
    await ledger.mark_unknown(old_b, LLMError("TIMEOUT"))
    await _age(engine, old_b, 48)

    orphan = await _insert_orphan_unknown(engine, 500, 26)

    assert await _budget(engine, KEY_A) == (400, 3_000 + 2_000 + 700)
    assert await _budget(engine, KEY_B) == (0, 4_000)

    report = await ledger.sweep_unknown(OLDER_THAN)

    assert report == SweepReport(calls=3, micro_usd=3_000 + 4_000 + 500, budget_keys=2)
    # 24h 이전 UNKNOWN: 상태 유지 + actual = est(정리 표시)
    for call_id, est in ((old_a, 3_000), (old_b, 4_000), (orphan, 500)):
        row = await _call(engine, call_id)
        assert (row["status"], row["actual_micro_usd"]) == ("UNKNOWN", est)
    # 최근 UNKNOWN 유지
    recent = await _call(engine, recent_a)
    assert (recent["status"], recent["actual_micro_usd"]) == ("UNKNOWN", None)
    # COMPLETE 불변, 열린 예약 불변
    assert await _call(engine, done_a) == done_before
    assert (await _call(engine, open_a))["status"] == "RESERVED"
    # 예산: reserved 누수 0 = 남은 열린 호출 est 합, spent += 정리액
    assert await _budget(engine, KEY_A) == (400 + 3_000, 2_000 + 700)
    assert (await _budget(engine, KEY_A))[1] == await _open_est(engine, KEY_A)
    assert await _budget(engine, KEY_B) == (4_000, 0)
    assert await _open_est(engine, KEY_B) == 0

    # 재실행 멱등
    again = await ledger.sweep_unknown(OLDER_THAN)
    assert again == SweepReport(calls=0, micro_usd=0, budget_keys=0)
    assert await _budget(engine, KEY_A) == (400 + 3_000, 2_000 + 700)
    assert await _budget(engine, KEY_B) == (4_000, 0)


async def test_정리된_UNKNOWN_에_늦은_settle_이_와도_이중_정산하지_않는다(engine: AsyncEngine):
    ledger = PostgresCallLedger(engine)
    call_id = await ledger.reserve(KEY_A, _spec(5_000, 0))
    await ledger.mark_unknown(call_id, LLMError("TIMEOUT"))
    await _age(engine, call_id, 25)

    assert (await ledger.sweep_unknown(OLDER_THAN)).calls == 1
    await ledger.settle(call_id, _result(1))

    assert await _budget(engine, KEY_A) == (5_000, 0)
    assert (await _call(engine, call_id))["actual_micro_usd"] == 5_000


async def test_오래된_RESERVED_는_UNKNOWN_으로_바꿔_정리하고_최근_RESERVED_는_유지한다(
    engine: AsyncEngine,
):
    ledger = PostgresCallLedger(engine)
    old = await ledger.reserve(KEY_A, _spec(6_000, 0))
    await _age(engine, old, 25)
    recent = await ledger.reserve(KEY_A, _spec(900, 1))
    await _age(engine, recent, 23)
    assert await _budget(engine, KEY_A) == (0, 6_900)

    report = await ledger.sweep_unknown(OLDER_THAN)

    assert report == SweepReport(calls=1, micro_usd=6_000, budget_keys=1, reserved_calls=1)
    row = await _call(engine, old)
    assert (row["status"], row["actual_micro_usd"]) == ("UNKNOWN", 6_000)
    assert row["finished_at"] is not None
    kept = await _call(engine, recent)
    assert (kept["status"], kept["actual_micro_usd"]) == ("RESERVED", None)
    # reserved 누수 0 · spent 반영
    assert await _budget(engine, KEY_A) == (6_000, 900)
    assert (await _budget(engine, KEY_A))[1] == await _open_est(engine, KEY_A)

    again = await ledger.sweep_unknown(OLDER_THAN)
    assert again == SweepReport(calls=0, micro_usd=0, budget_keys=0, reserved_calls=0)
    assert await _budget(engine, KEY_A) == (6_000, 900)

    # 정리된 행에 늦은 settle 이 와도 이중 정산하지 않는다
    await ledger.settle(old, _result(1))
    assert await _budget(engine, KEY_A) == (6_000, 900)


async def test_대상이_없으면_빈_리포트(engine: AsyncEngine):
    report = await PostgresCallLedger(engine).sweep_unknown(OLDER_THAN)
    assert report == SweepReport(calls=0, micro_usd=0, budget_keys=0)
