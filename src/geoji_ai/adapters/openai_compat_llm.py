"""OpenAI 호환 벤더 어댑터(03 §3.5). `LLMPort` 구현.

OpenAI 와 xAI 는 같은 chat.completions API 를 쓴다. 벤더마다 클라이언트를 하나 만든다.
재시도는 하지 않는다(`max_retries=0`). 재시도·백오프·예산 판단은 호출자가 한다
(`domain/retries.py`). 실패는 06 §3.1 표대로 `LLMError(kind)` 로만 낸다.
성공이 아닌 응답(잘림·거절·파싱 실패)은 `usage`·`cost` 를 예외에 실어 원장이 쓰게 한다.
`settings` → 어댑터 조립은 `adapters/llm_router.py`.
"""

from __future__ import annotations

import json
import math
import time
from decimal import Decimal
from typing import Any, Literal

import openai
import pydantic

from geoji_ai.ports.llm import Cost, LLMError, LLMResult, LLMRole, Usage

Vendor = Literal["openai", "xai"]

# $ per 1M tokens (입력, 출력). 2026-09-07 각 벤더 가격표(03 §3.5).
PRICE_PER_1M_USD: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "grok-4.20-0309-non-reasoning": (1.25, 2.50),
}
KRW_PER_USD = 1450

# xAI usage.cost_in_usd_ticks: 1 tick = 1e-10 USD → 1 micro-USD = 10_000 ticks.
TICKS_PER_MICRO_USD = 10_000


class OpenAICompatLLM:
    """`openai.AsyncOpenAI` 로 strict json_schema 호출을 한다."""

    def __init__(
        self,
        vendor: Vendor,
        *,
        api_key: str,
        base_url: str | None = None,
        model_id: str,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.vendor: Vendor = vendor
        self.model_id = model_id
        if client is None:
            client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        elif client.max_retries != 0:
            client = client.with_options(max_retries=0)
        self.client = client

    async def structured_call(
        self,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": role, "strict": True, "schema": schema},
        }
        started = time.perf_counter()
        try:
            raw = await self.client.chat.completions.with_raw_response.create(
                model=self.model_id,
                messages=messages,  # type: ignore[arg-type]
                response_format=response_format,  # type: ignore[arg-type]
                max_tokens=max_output_tokens,
                timeout=timeout_s,
            )
            completion = raw.parse()
        except openai.APITimeoutError as exc:
            raise LLMError("TIMEOUT", message=str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise LLMError("TRANSPORT", message=str(exc)) from exc
        except openai.RateLimitError as exc:
            raise LLMError(
                "RATE_LIMIT",
                retry_after_s=_retry_after_s(exc.response.headers),
                message=str(exc),
            ) from exc
        except openai.APIStatusError as exc:
            kind = _status_kind(exc.status_code)
            raise LLMError(
                kind,
                retry_after_s=_retry_after_s(exc.response.headers) if kind == "SERVER" else None,
                message=str(exc),
            ) from exc
        except (
            openai.APIResponseValidationError,
            pydantic.ValidationError,
            json.JSONDecodeError,
        ) as exc:
            # HTTP 본문 자체가 깨져 completion 이 없다. usage 를 얻을 수 없다.
            raise LLMError("PARSE", message=str(exc)) from exc
        latency_ms = math.ceil((time.perf_counter() - started) * 1000)

        usage = _usage(completion.usage)
        # 응답의 model 은 날짜 접미사가 붙을 수 있어 단가표는 설정한 model_id 로 찾는다
        cost = _cost(completion.usage, usage, self.model_id)

        if not completion.choices:
            raise LLMError("PARSE", message="응답에 choices 가 없다", usage=usage, cost=cost)
        choice = completion.choices[0]
        message = choice.message
        if getattr(message, "refusal", None) or choice.finish_reason == "content_filter":
            raise LLMError("REFUSAL", message="벤더가 응답을 거절했다", usage=usage, cost=cost)
        if choice.finish_reason == "length":
            # 잘린 JSON 은 스키마 실패로 본다(06 §3.1, §14.1).
            raise LLMError(
                "SCHEMA", message="출력이 max_tokens 에서 잘렸다", usage=usage, cost=cost
            )
        try:
            output = _parse_output(message.content)
        except LLMError as exc:
            raise LLMError(exc.kind, message=str(exc), usage=usage, cost=cost) from exc

        return LLMResult(
            output=output,
            stop_reason="stop",
            usage=usage,
            cost=cost,
            provider_request_id=raw.headers.get("x-request-id") or completion.id or None,
            model_id=self.model_id,
            vendor=self.vendor,
            latency_ms=latency_ms,
        )


def _parse_output(content: str | None) -> dict[str, Any]:
    if content is None:
        raise LLMError("PARSE", message="본문이 비었다")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError("PARSE", message=f"본문이 JSON 이 아니다: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("PARSE", message="본문이 JSON 객체가 아니다")
    return value


def _status_kind(status: int) -> Literal["AUTH", "SCHEMA", "SERVER"]:
    # 401·403 → AUTH(재시도 없음, degraded 카운트 안 함). strict 스키마 거절 400 과 그 밖 4xx 는
    # 요청이 틀린 것이라 SCHEMA. 5xx(와 표 밖 상태)는 SERVER.
    # 429 는 `RateLimitError` 가 먼저 받는다.
    if status in (401, 403):
        return "AUTH"
    if 400 <= status < 500:
        return "SCHEMA"
    return "SERVER"


def _retry_after_s(headers: Any) -> float | None:
    value = headers.get("retry-after")
    if value is not None:
        try:
            return float(value)
        except ValueError:
            pass
    value_ms = headers.get("retry-after-ms")
    if value_ms is not None:
        try:
            return float(value_ms) / 1000
        except ValueError:
            pass
    return None


def _extra(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None) or {}
        value = extra.get(name)
    return value


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    prompt_details = getattr(raw, "prompt_tokens_details", None)
    completion_details = getattr(raw, "completion_tokens_details", None)
    return Usage(
        prompt_tokens=raw.prompt_tokens or 0,
        completion_tokens=raw.completion_tokens or 0,
        reasoning_tokens=(getattr(completion_details, "reasoning_tokens", None) or 0),
        cached_tokens=(getattr(prompt_details, "cached_tokens", None) or 0),
    )


def _cost(raw: Any, usage: Usage, model: str) -> Cost:
    ticks = _extra(raw, "cost_in_usd_ticks") if raw is not None else None
    if ticks is not None:
        ticks = int(ticks)
        return Cost(ticks=ticks, micro_usd=ticks // TICKS_PER_MICRO_USD, source="usage")
    price = PRICE_PER_1M_USD.get(model)
    if price is None:
        return Cost()
    # $/1M 토큰 × 토큰 = micro-USD. completion_tokens 는 추론 토큰을 포함한다(OpenAI).
    micro = Decimal(usage.prompt_tokens) * Decimal(str(price[0])) + Decimal(
        usage.completion_tokens
    ) * Decimal(str(price[1]))
    return Cost(ticks=None, micro_usd=int(micro), source="table")
