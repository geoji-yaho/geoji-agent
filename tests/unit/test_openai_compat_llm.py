"""`adapters/openai_compat_llm.py` 단위 테스트(03 §3.5, VF-04).

httpx `MockTransport` 로 chat.completions 응답을 위조한다. 네트워크·실제 키 없음.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import openai
import pytest

from geoji_ai.adapters.openai_compat_llm import (
    KRW_PER_USD,
    PRICE_PER_1M_USD,
    OpenAICompatLLM,
)
from geoji_ai.ports.llm import LLMError, LLMPort

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
SCHEMA = {
    "type": "object",
    "properties": {"sentence": {"type": "string", "enum": ["oneDay"]}},
    "required": ["sentence"],
    "additionalProperties": False,
}
OUTPUT = {"sentence": "oneDay"}
DEFAULT_USAGE = {
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "total_tokens": 1500,
    "prompt_tokens_details": {"cached_tokens": 200},
    "completion_tokens_details": {"reasoning_tokens": 30},
}

Handler = Callable[[httpx.Request], httpx.Response]


def completion_body(
    *,
    model: str = "gpt-5.6-luna",
    content: str | None = json.dumps(OUTPUT),
    finish_reason: str = "stop",
    refusal: str | None = None,
    usage: dict | None = None,
) -> dict:
    return {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "created": 1757000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": DEFAULT_USAGE if usage is None else usage,
    }


class Recorder:
    def __init__(self, respond: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


def make_llm(
    respond: Handler,
    *,
    vendor: str = "openai",
    model_id: str = "gpt-5.6-luna",
    client_max_retries: int = 0,
) -> tuple[OpenAICompatLLM, Recorder]:
    recorder = Recorder(respond)
    client = openai.AsyncOpenAI(
        api_key="test",
        base_url="https://llm.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
        max_retries=client_max_retries,
    )
    llm = OpenAICompatLLM(
        vendor,  # type: ignore[arg-type]
        api_key="test",
        model_id=model_id,
        client=client,
    )
    return llm, recorder


def ok(body: dict, headers: dict | None = None) -> Handler:
    return lambda _req: httpx.Response(200, json=body, headers=headers or {})


def raising(exc_type: type[httpx.TransportError], message: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type(message, request=request)

    return handler


async def call(llm: OpenAICompatLLM, role: str = "sentencing"):
    return await llm.structured_call(
        role=role,  # type: ignore[arg-type]
        messages=MESSAGES,
        schema=SCHEMA,
        timeout_s=3.0,
        max_output_tokens=400,
    )


def test_implements_port_and_constants() -> None:
    llm, _ = make_llm(ok(completion_body()))
    assert isinstance(llm, LLMPort)
    assert KRW_PER_USD == 1450
    assert PRICE_PER_1M_USD == {
        "gpt-5.6-luna": (0.20, 1.20),
        "gpt-5.6-terra": (2.00, 12.00),
        "grok-4.20-0309-non-reasoning": (1.25, 2.50),
    }


# ① 요청 본문
async def test_request_body_strict_json_schema() -> None:
    llm, rec = make_llm(ok(completion_body()))
    await call(llm, role="sentencing")

    assert len(rec.requests) == 1
    body = json.loads(rec.requests[0].content)
    assert body["model"] == "gpt-5.6-luna"
    assert body["max_tokens"] == 400
    assert body["messages"] == MESSAGES
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "sentencing"
    assert fmt["json_schema"]["schema"] == SCHEMA


# ② stop → output·usage 4종·latency
async def test_stop_maps_output_usage_latency() -> None:
    llm, _ = make_llm(ok(completion_body(), headers={"x-request-id": "req_123"}))
    result = await call(llm)

    assert result.stop_reason == "stop"
    assert result.output == OUTPUT
    assert result.usage.prompt_tokens == 1000
    assert result.usage.completion_tokens == 500
    assert result.usage.reasoning_tokens == 30
    assert result.usage.cached_tokens == 200
    assert result.latency_ms > 0
    assert result.provider_request_id == "req_123"
    assert result.model_id == "gpt-5.6-luna"
    assert result.vendor == "openai"


async def test_usage_details_missing_are_zero() -> None:
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    llm, _ = make_llm(ok(completion_body(usage=usage)))
    result = await call(llm)
    assert result.usage.reasoning_tokens == 0
    assert result.usage.cached_tokens == 0


# 06 §3.1 오류 6상황. 케이스 번호는 작업 6 스펙(feat-06-adapter-retries) ①~⑦.
async def test_length_raises_schema_with_usage() -> None:
    """06 ① finish_reason=length → SCHEMA + usage·cost."""
    llm, _ = make_llm(ok(completion_body(content='{"sentence": "one', finish_reason="length")))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SCHEMA"
    assert exc.value.usage is not None
    assert exc.value.usage.prompt_tokens == 1000
    assert exc.value.usage.completion_tokens == 500
    assert exc.value.cost is not None
    assert exc.value.cost.micro_usd == 800


async def test_length_with_complete_json_still_schema() -> None:
    """06 ① 잘림 신호가 있으면 본문이 파싱돼도 결과로 쓰지 않는다."""
    llm, _ = make_llm(ok(completion_body(finish_reason="length")))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SCHEMA"


async def test_message_refusal_raises_refusal() -> None:
    """06 ② provider refusal(message.refusal) → REFUSAL."""
    llm, _ = make_llm(ok(completion_body(content=None, refusal="I can't help with that.")))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "REFUSAL"
    assert exc.value.usage is not None
    assert exc.value.usage.prompt_tokens == 1000


async def test_content_filter_raises_refusal() -> None:
    """06 ② finish_reason=content_filter → REFUSAL."""
    llm, _ = make_llm(ok(completion_body(content=None, finish_reason="content_filter")))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "REFUSAL"


# ④ xAI ticks → micro-USD 내림
async def test_xai_cost_in_usd_ticks() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
        "cost_in_usd_ticks": 123456789,
    }
    llm, _ = make_llm(
        ok(completion_body(model="grok-4.20-0309-non-reasoning", usage=usage)),
        vendor="xai",
        model_id="grok-4.20-0309-non-reasoning",
    )
    result = await call(llm, role="writer")
    assert result.cost.ticks == 123456789
    assert result.cost.micro_usd == 12345
    assert result.cost.source == "usage"
    assert result.vendor == "xai"


# ⑤ ticks 없음 → 단가표
async def test_openai_cost_from_price_table() -> None:
    llm, _ = make_llm(ok(completion_body()))
    result = await call(llm)
    # 1000 × 0.20 + 500 × 1.20 = 800 micro-USD
    assert result.cost.micro_usd == 800
    assert result.cost.ticks is None
    assert result.cost.source == "table"


async def test_unknown_model_cost_is_unknown() -> None:
    llm, _ = make_llm(ok(completion_body(model="some-new-model")), model_id="some-new-model")
    result = await call(llm)
    assert result.cost.source == "unknown"
    assert result.cost.micro_usd is None
    assert result.cost.ticks is None


# ⑥ 오류 → LLMError(kind)
async def test_rate_limit_with_retry_after() -> None:
    llm, _ = make_llm(
        lambda _r: httpx.Response(
            429, json={"error": {"message": "slow down"}}, headers={"retry-after": "3"}
        )
    )
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "RATE_LIMIT"
    assert exc.value.retry_after_s == 3


async def test_rate_limit_retry_after_2() -> None:
    """06 ③ 429 + Retry-After 2 → RATE_LIMIT, retry_after_s=2, usage 없음."""
    llm, _ = make_llm(
        lambda _r: httpx.Response(
            429, json={"error": {"message": "slow down"}}, headers={"retry-after": "2"}
        )
    )
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "RATE_LIMIT"
    assert exc.value.retry_after_s == 2
    assert exc.value.usage is None


async def test_rate_limit_without_retry_after_is_none() -> None:
    llm, _ = make_llm(lambda _r: httpx.Response(429, json={"error": {"message": "slow down"}}))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "RATE_LIMIT"
    assert exc.value.retry_after_s is None


async def test_server_error() -> None:
    llm, _ = make_llm(lambda _r: httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SERVER"


async def test_503_is_server() -> None:
    """06 ④ 503 → SERVER. Retry-After 가 없으면 retry_after_s None."""
    llm, _ = make_llm(lambda _r: httpx.Response(503, json={"error": {"message": "overloaded"}}))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SERVER"
    assert exc.value.retry_after_s is None
    assert exc.value.usage is None


async def test_503_with_retry_after() -> None:
    """06 ④ 5xx 에 Retry-After 가 있으면 넘긴다."""
    llm, _ = make_llm(
        lambda _r: httpx.Response(
            503, json={"error": {"message": "overloaded"}}, headers={"retry-after": "1"}
        )
    )
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SERVER"
    assert exc.value.retry_after_s == 1


async def test_connection_error_is_transport() -> None:
    """06 ⑤ 연결 오류 → TRANSPORT, usage None."""
    llm, _ = make_llm(raising(httpx.ConnectError, "refused"))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "TRANSPORT"
    assert exc.value.usage is None
    assert exc.value.cost is None


async def test_strict_schema_rejection_is_schema() -> None:
    body = {
        "error": {
            "message": "Invalid schema for response_format 'sentencing'",
            "type": "invalid_request_error",
            "param": "response_format",
            "code": None,
        }
    }
    llm, _ = make_llm(lambda _r: httpx.Response(400, json=body))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SCHEMA"
    # 400 거절 응답에는 usage 가 없다
    assert exc.value.usage is None


async def test_non_json_content_is_parse() -> None:
    """06 ⑥ 본문이 JSON 이 아님 → PARSE + usage·cost."""
    llm, _ = make_llm(ok(completion_body(content="판결: 유죄")))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "PARSE"
    assert exc.value.usage is not None
    assert exc.value.usage.completion_tokens == 500
    assert exc.value.cost is not None
    assert exc.value.cost.source == "table"


async def test_broken_json_content_is_parse_with_usage() -> None:
    """06 ⑥ 깨진 JSON → PARSE + usage."""
    llm, _ = make_llm(ok(completion_body(content='{"sentence": "oneDay"')))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "PARSE"
    assert exc.value.usage is not None
    assert exc.value.usage.prompt_tokens == 1000


async def test_json_array_content_is_parse_with_usage() -> None:
    llm, _ = make_llm(ok(completion_body(content="[1, 2]")))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "PARSE"
    assert exc.value.usage is not None


async def test_non_json_http_body_is_parse() -> None:
    llm, _ = make_llm(
        lambda _r: httpx.Response(
            200, content=b"<html>gateway</html>", headers={"content-type": "application/json"}
        )
    )
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "PARSE"
    # completion 자체가 없어 usage 를 얻을 수 없다
    assert exc.value.usage is None


async def test_timeout() -> None:
    """06 ⑤ timeout → TIMEOUT, usage None."""
    llm, _ = make_llm(raising(httpx.ReadTimeout, "timed out"))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "TIMEOUT"
    assert exc.value.usage is None
    assert exc.value.cost is None


# ⑦ max_retries=0 — 실패 시 요청 1개(06 ⑦ SDK 자동 재시도 0)
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_no_retry_on_status_error(status: int) -> None:
    # 주입 클라이언트가 재시도를 켜 두어도 어댑터가 0 으로 고정한다
    llm, rec = make_llm(
        lambda _r: httpx.Response(status, json={"error": {"message": "x"}}),
        client_max_retries=2,
    )
    with pytest.raises(LLMError):
        await call(llm)
    assert len(rec.requests) == 1


async def test_no_retry_on_connection_error() -> None:
    llm, rec = make_llm(raising(httpx.ConnectError, "refused"), client_max_retries=2)
    with pytest.raises(LLMError):
        await call(llm)
    assert len(rec.requests) == 1


def test_default_clients_per_vendor() -> None:
    openai_llm = OpenAICompatLLM("openai", api_key="k", model_id="gpt-5.6-luna")
    assert openai_llm.client.max_retries == 0
    assert str(openai_llm.client.base_url).startswith("https://api.openai.com")

    xai_llm = OpenAICompatLLM(
        "xai",
        api_key="k",
        base_url="https://api.x.ai/v1",
        model_id="grok-4.20-0309-non-reasoning",
    )
    assert xai_llm.client.max_retries == 0
    assert str(xai_llm.client.base_url).startswith("https://api.x.ai/v1")


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_status_is_auth(status: int) -> None:
    """401·403 → AUTH(재시도 없음·degraded 카운트 안 함, 9/14). Retry-After 를 싣지 않는다."""
    llm, recorder = make_llm(
        lambda _r: httpx.Response(
            status, json={"error": {"message": "no"}}, headers={"retry-after": "1"}
        )
    )
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "AUTH"
    assert exc.value.retry_after_s is None
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("status", [404, 409, 422])
async def test_other_4xx_is_schema(status: int) -> None:
    """400 밖 4xx 도 요청 오류라 SCHEMA(9/14)."""
    llm, _ = make_llm(lambda _r: httpx.Response(status, json={"error": {"message": "bad"}}))
    with pytest.raises(LLMError) as exc:
        await call(llm)
    assert exc.value.kind == "SCHEMA"
