"""가짜 LLM 어댑터 (01 §3.6, §4.1·§4.2).

시나리오 8종 · `calls[]` 기록 · 역할 6종의 정상 출력이 strict 스키마를 통과하는지.
네트워크·키를 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import inspect
import time

import jsonschema
import pytest

from geoji_ai.adapters.fake_llm import (
    RATE_LIMIT_RETRY_AFTER_S,
    FakeLLM,
    FakeScenario,
)
from geoji_ai.ports.llm import LLMError, LLMPort
from tests.conftest import load_fixture

ROLES = ("intake", "context", "banter", "sentencing", "writer", "evaluator")

#: 스키마 모양만 흉내 낸 최소 스텁. strict 스키마 검증은 아래 `test_strict_schema_*` 가 한다.
STUB_SCHEMA: dict[str, dict] = {
    "intake": {"type": "object"},
    "context": {"type": "object"},
    "banter": {"type": "object"},
    "sentencing": {"type": "object"},
    "writer": {"type": "object", "properties": {"intensity": {"enum": ["spicy"]}}},
    "evaluator": {
        "type": "object",
        "properties": {
            "texts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"intensity": {"enum": ["mild", "spicy", "hell"]}},
                },
            }
        },
    },
}


def writer_stub(intensity: str) -> dict:
    return {"type": "object", "properties": {"intensity": {"enum": [intensity]}}}


async def call(fake: FakeLLM, role: str, schema: dict | None = None):
    return await fake.structured_call(
        role=role,
        messages=[{"role": "user", "content": "택시 12,000원"}],
        schema=schema if schema is not None else STUB_SCHEMA[role],
        timeout_s=3.0,
        max_output_tokens=700,
    )


# 1. 정상 -----------------------------------------------------------------


def test_fake_is_llm_port():
    assert isinstance(FakeLLM(), LLMPort)
    # runtime_checkable 은 메서드 이름만 본다. 시그니처도 포트와 같아야 한다.
    assert inspect.signature(FakeLLM.structured_call) == inspect.signature(LLMPort.structured_call)


@pytest.mark.parametrize("role", ROLES)
async def test_ok_returns_fixture_output(role: str):
    fake = FakeLLM()
    result = await call(fake, role)
    assert result.stop_reason == "stop"
    assert isinstance(result.output, dict) and result.output
    assert result.vendor == "fake"
    assert result.cost.source == "unknown"


async def test_ok_intake_output_is_the_fixture():
    fake = FakeLLM()
    result = await call(fake, "intake")
    assert result.output == load_fixture("intake-taxi-pass")


async def test_ok_sentencing_output_is_the_fixture():
    fake = FakeLLM()
    result = await call(fake, "sentencing")
    assert result.output == load_fixture("sentencing-taxi")


async def test_ok_context_output_is_the_fixture():
    fake = FakeLLM()
    result = await call(fake, "context")
    assert result.output == load_fixture("context-taxi")


async def test_writer_returns_requested_intensity():
    fake = FakeLLM()
    result = await call(fake, "writer", writer_stub("hell"))
    assert result.output is not None
    assert result.output["intensity"] == "hell"
    assert "meme_tag" in result.output
    assert "meme_hints" in result.output


async def test_banter_candidates_use_fits():
    fake = FakeLLM()
    result = await call(fake, "banter")
    assert result.output is not None
    candidates = result.output["candidates"]
    assert candidates
    for candidate in candidates:
        assert "intensity" not in candidate
        assert isinstance(candidate["fits"], list)
        assert {"text", "strategy", "fits", "evidence_labels"} == set(candidate)


async def test_evaluator_texts_match_requested_intensities():
    fake = FakeLLM()
    result = await call(fake, "evaluator")
    assert result.output is not None
    assert [text["intensity"] for text in result.output["texts"]] == ["mild", "spicy", "hell"]


async def test_outputs_override_wins_over_fixture():
    fake = FakeLLM(outputs={"intake": {"status": "BLOCKED"}})
    result = await call(fake, "intake")
    assert result.output == {"status": "BLOCKED"}


# 2. 시나리오 8종 ---------------------------------------------------------


async def test_scenario_timeout():
    fake = FakeLLM(FakeScenario.TIMEOUT)
    with pytest.raises(LLMError) as excinfo:
        await call(fake, "writer")
    assert excinfo.value.kind == "TIMEOUT"


async def test_scenario_rate_limit_carries_retry_after():
    fake = FakeLLM(FakeScenario.RATE_LIMIT)
    with pytest.raises(LLMError) as excinfo:
        await call(fake, "writer")
    assert excinfo.value.kind == "RATE_LIMIT"
    assert excinfo.value.retry_after_s == RATE_LIMIT_RETRY_AFTER_S
    assert "429" in str(excinfo.value)


async def test_scenario_refusal_returns_result_without_output():
    fake = FakeLLM(FakeScenario.REFUSAL)
    result = await call(fake, "writer")
    assert result.stop_reason == "refusal"
    assert result.output is None


async def test_scenario_truncated_returns_max_tokens():
    fake = FakeLLM(FakeScenario.TRUNCATED)
    result = await call(fake, "writer")
    assert result.stop_reason == "max_tokens"
    assert result.output is None


async def test_scenario_parse_error():
    fake = FakeLLM(FakeScenario.PARSE_ERROR)
    with pytest.raises(LLMError) as excinfo:
        await call(fake, "writer")
    assert excinfo.value.kind == "PARSE"


async def test_scenario_schema_mismatch():
    fake = FakeLLM(FakeScenario.SCHEMA_MISMATCH)
    with pytest.raises(LLMError) as excinfo:
        await call(fake, "writer")
    assert excinfo.value.kind == "SCHEMA"


async def test_scenario_intensity_fail_only_hits_that_intensity():
    fake = FakeLLM(FakeScenario.INTENSITY_FAIL)
    with pytest.raises(LLMError) as excinfo:
        await call(fake, "writer", writer_stub("hell"))
    assert excinfo.value.kind == "SCHEMA"

    for intensity in ("mild", "spicy"):
        result = await call(fake, "writer", writer_stub(intensity))
        assert result.stop_reason == "stop"
        assert result.output is not None
        assert result.output["intensity"] == intensity


async def test_scenario_intensity_fail_spares_other_roles():
    fake = FakeLLM(FakeScenario.INTENSITY_FAIL)
    for role in ("intake", "context", "banter", "sentencing", "evaluator"):
        result = await call(fake, role)
        assert result.stop_reason == "stop"


async def test_scenario_intensity_fail_honours_fail_intensity_argument():
    fake = FakeLLM(FakeScenario.INTENSITY_FAIL, fail_intensity="mild")
    with pytest.raises(LLMError):
        await call(fake, "writer", writer_stub("mild"))
    result = await call(fake, "writer", writer_stub("hell"))
    assert result.output is not None


# 3. 검수관 위반 코드 주입 ------------------------------------------------


async def test_violation_codes_are_injected():
    fake = FakeLLM(violation_codes=["PERSONAL_ATTACK"])
    result = await call(fake, "evaluator")
    assert result.output is not None
    for text in result.output["texts"]:
        assert text["pass"] is False
        assert [violation["code"] for violation in text["violations"]] == ["PERSONAL_ATTACK"]


async def test_without_violation_codes_evaluation_passes():
    fake = FakeLLM()
    result = await call(fake, "evaluator")
    assert result.output is not None
    assert all(text["pass"] is True for text in result.output["texts"])
    assert all(text["violations"] == [] for text in result.output["texts"])


# 4. calls[] 기록 ---------------------------------------------------------


async def test_calls_record_order_and_arguments():
    fake = FakeLLM()
    assert fake.calls == []
    await call(fake, "intake")
    await call(fake, "context")
    await call(fake, "banter")
    await call(fake, "writer")

    assert len(fake.calls) == 4
    assert [recorded.role for recorded in fake.calls] == ["intake", "context", "banter", "writer"]

    first = fake.calls[0]
    assert first.schema == STUB_SCHEMA["intake"]
    assert first.messages == [{"role": "user", "content": "택시 12,000원"}]
    assert first.timeout_s == 3.0
    assert first.max_output_tokens == 700
    assert first.scenario is FakeScenario.OK


async def test_calls_record_failed_calls_too():
    fake = FakeLLM(FakeScenario.TIMEOUT)
    for _ in range(2):
        with pytest.raises(LLMError):
            await call(fake, "sentencing")
    assert len(fake.calls) == 2
    assert all(recorded.scenario is FakeScenario.TIMEOUT for recorded in fake.calls)


# 5. 지연 -----------------------------------------------------------------


async def test_latency_is_awaited_not_blocking():
    fake = FakeLLM(latency_ms=30)
    started = time.perf_counter()
    results = await asyncio.gather(*[call(fake, "intake") for _ in range(4)])
    elapsed = time.perf_counter() - started

    assert all(result.latency_ms == 30 for result in results)
    assert elapsed >= 0.03
    assert elapsed < 0.12  # 블로킹 sleep 이면 4회 직렬로 0.12초를 넘는다
    assert len(fake.calls) == 4


# 6. strict 스키마 검증 (A 의 `contracts/llm_schemas.py`) --------------------


def strict_schema(role: str) -> dict:
    from geoji_ai.contracts import llm_schemas

    match role:
        case "intake":
            return llm_schemas.intake_schema("INITIAL")
        case "context":
            return llm_schemas.context_schema()
        case "banter":
            return llm_schemas.banter_schema()
        case "sentencing":
            return llm_schemas.sentencing_schema(["probation", "oneDay"])
        case "writer":
            return llm_schemas.writer_schema(["spicy"], ["CONVERSION"])
        case "evaluator":
            return llm_schemas.evaluator_schema(["mild", "spicy", "hell"])
    raise AssertionError(role)


@pytest.mark.parametrize("role", ROLES)
async def test_strict_schema_validates_fake_output(role: str):
    schema = strict_schema(role)
    fake = FakeLLM()
    result = await call(fake, role, schema)
    jsonschema.validate(instance=result.output, schema=schema)
