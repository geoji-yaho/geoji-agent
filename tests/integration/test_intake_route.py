"""intake 라우트 + 실제 Postgres 원장(07 §3.4, §4.2).

인증 401 / 원장 행(`post_id=submission:{id}`, node `intake`) / 같은 제출 3번째 호출이 cap(1,034
micro-USD)에 막혀 FALLBACK·모델 호출 0. 응답 에코(`submission_id`·`payload_hash`)는 계약에 없어
테스트하지 않는다.

비용: FakeLLM 은 `Cost()`(비용 모름)를 돌려주고 원장은 그때 `est_max` 를 spent 에 더한다. intake
est_max 는 약 531 이라 두 번째 호출부터 막힌다(531 × 2 > 1,034). 실제 어댑터는 사용량 × 단가표로
비용을 채우므로(`openai_compat_llm._cost`), 이 테스트는 FakeLLM 을 감싸 **같은 단가표 규칙**으로
`Cost.micro_usd` 를 채운다(`TableCostLLM`). 값을 만들지 않고 가짜 사용량에 단가표를 곱할 뿐이다.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geoji_ai.adapters.fake_llm import FakeLLM
from geoji_ai.adapters.llm_router import RoleRoutedLLM, price_for
from geoji_ai.adapters.postgres_call_ledger import PostgresCallLedger
from geoji_ai.api.app import SUBMISSION_CAP_MICRO_USD, create_app
from geoji_ai.application.llm_gateway import LLMGateway, rough_prompt_tokens
from geoji_ai.contracts.intake import IntakeRequest
from geoji_ai.core.config import Settings
from geoji_ai.domain.budget import est_max_micro_usd
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.graphs.intake import _messages
from geoji_ai.ports.llm import Cost, LLMResult

TOKEN = "intake-int-token-3b9f"
URL = "/internal/v1/intake"
SETTINGS = Settings(
    _env_file=None,
    SERVICE_AUTH_TOKEN=TOKEN,
    OPENAI_API_KEY="",
    XAI_API_KEY="",
    DATABASE_URL="",
)
MODEL = SETTINGS.MODEL_JUDGMENT

#: 과장된 무엇을 → NEEDS_CLARIFICATION. 출력 길이가 가짜 completion token 수를 정한다.
NEEDS_OUTPUT: dict[str, Any] = {
    "schema_version": 1,
    "mode": "INITIAL",
    "status": "NEEDS_CLARIFICATION",
    "item_review": {"status": "EXAGGERATED", "suggested_item": "아이스크림"},
    "message": "무엇을 사셨는지 품목 이름으로 솔직하게 알려 주세요.",
    "category_review": {"status": "OK", "suggested_category": "카페/간식", "confidence": 0.6},
    "injection_detected": False,
}


def table_cost(prompt_tokens: int, completion_tokens: int) -> int:
    """`openai_compat_llm._cost` 의 단가표 분기와 같은 계산(내림)."""
    price_in, price_out = price_for(MODEL)  # type: ignore[misc]
    return int(
        Decimal(prompt_tokens) * Decimal(str(price_in))
        + Decimal(completion_tokens) * Decimal(str(price_out))
    )


class TableCostLLM:
    """FakeLLM 결과의 `cost` 를 단가표 × 가짜 사용량으로 채운다."""

    def __init__(self, fake: FakeLLM) -> None:
        self.fake = fake

    async def structured_call(self, **kwargs: Any) -> LLMResult:
        result = await self.fake.structured_call(**kwargs)
        micro = table_cost(result.usage.prompt_tokens, result.usage.completion_tokens)
        return dataclasses.replace(result, cost=Cost(micro_usd=micro, source="table"))


def body(item: str, *, submission_id: str = "sub-int-1", mode: str = "INITIAL") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "payload_hash": "hash-int",
        "mode": mode,
        "post_type": "spent",
        "amount_krw": 12000,
        "category": "기타",
        "item": item,
        "reason": "바쁘다바빠 현대사회",
    }


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM(outputs={"intake": NEEDS_OUTPUT})


@pytest.fixture
async def client(engine: AsyncEngine, fake: FakeLLM) -> AsyncIterator[httpx.AsyncClient]:
    router = RoleRoutedLLM({"openai": TableCostLLM(fake)}, models={"openai": MODEL})
    ledger = PostgresCallLedger(engine, cap_micro_usd=SUBMISSION_CAP_MICRO_USD)
    gateway = LLMGateway(router, ledger, VendorHealth(), price_for)
    app = create_app(SETTINGS, intake_llm=gateway)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://geoji-ai"
    ) as http:
        yield http


async def llm_call_rows(engine: AsyncEngine, post_id: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT post_id, node, call_index, status, actual_micro_usd "
                    "FROM ai.llm_calls WHERE post_id = :post_id ORDER BY started_at"
                ),
                {"post_id": post_id},
            )
        ).mappings()
        return [dict(row) for row in rows]


async def test_토큰이_없으면_401(client: httpx.AsyncClient, fake: FakeLLM):
    response = await client.post(URL, json=body("단비 같은 쾌락"))

    assert response.status_code == 401
    assert fake.calls == []


async def test_200_뒤_제출_예산_키로_원장_행이_남는다(
    client: httpx.AsyncClient, engine: AsyncEngine
):
    response = await client.post(URL, json=body("단비 같은 쾌락"), headers=auth())

    assert response.status_code == 200
    result = response.json()
    assert result["intake_source"] == "AI"
    assert result["status"] == "NEEDS_CLARIFICATION"

    rows = await llm_call_rows(engine, "submission:sub-int-1")
    assert len(rows) == 1
    assert rows[0]["node"] == "intake"
    assert rows[0]["call_index"] == 0
    assert rows[0]["status"] == "COMPLETE"


def _precondition() -> tuple[int, int]:
    """(est_max, 호출 1회 비용). 2회는 허용, 3회째는 cap 초과가 되는 값인지 확인한다."""
    req = IntakeRequest.model_validate(body("단비 같은 쾌락 1"))
    messages = _messages(req, False)
    est = est_max_micro_usd(
        rough_prompt_tokens(messages), SETTINGS.INTAKE_MAX_OUTPUT_TOKENS, *price_for(MODEL)
    )
    completion = len(json.dumps(NEEDS_OUTPUT, ensure_ascii=False)) // 4
    cost = table_cost(rough_prompt_tokens(messages), completion)
    return est, cost


async def test_같은_제출_3번째_호출은_cap_에_막혀_FALLBACK(
    client: httpx.AsyncClient, engine: AsyncEngine, fake: FakeLLM
):
    est, cost = _precondition()
    cap = SUBMISSION_CAP_MICRO_USD
    assert cost + est <= cap < 2 * cost + est, (
        f"전제 불성립: est_max={est}, 1회 비용={cost}, cap={cap}. "
        "프롬프트·출력 길이가 바뀌면 이 계산을 다시 본다(보고서 cap 계산)"
    )

    statuses: list[str] = []
    for index in (1, 2, 3):
        # node_results 재사용을 피하려고 호출마다 item 을 달리한다.
        response = await client.post(URL, json=body(f"단비 같은 쾌락 {index}"), headers=auth())
        assert response.status_code == 200
        statuses.append(response.json()["intake_source"])
        if index == 2:
            calls_after_two = len(fake.calls)

    assert statuses == ["AI", "AI", "FALLBACK"]
    assert calls_after_two == 2
    assert len(fake.calls) == 2

    rows = await llm_call_rows(engine, "submission:sub-int-1")
    assert len(rows) == 2
    assert all(row["status"] == "COMPLETE" for row in rows)
    async with engine.connect() as conn:
        budget = (
            (
                await conn.execute(
                    text(
                        "SELECT cap_micro_usd, spent_micro_usd, reserved_micro_usd "
                        "FROM ai.case_budgets WHERE post_id = 'submission:sub-int-1'"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert budget["cap_micro_usd"] == cap
    assert budget["spent_micro_usd"] == 2 * cost
    assert budget["reserved_micro_usd"] == 0
