"""LLM 포트 (01 §3.6).

어댑터가 구현한다. 이 모듈은 벤더 SDK(openai 등)를 import 하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

# 역할 6종. 프롬프트·strict 스키마·원장 노드 이름이 이 값을 공유한다.
LLMRole = Literal["intake", "context", "banter", "sentencing", "writer", "evaluator"]

StopReason = Literal["stop", "max_tokens", "refusal"]
CostSource = Literal["usage", "table", "unknown"]
LLMErrorKind = Literal[
    "TIMEOUT",
    "RATE_LIMIT",
    "SERVER",
    "TRANSPORT",
    "REFUSAL",
    "SCHEMA",
    "PARSE",
    # 401·403. 재시도 없음, degraded 카운트 안 함(06 §3.1, PR #19 미결 → 9/14).
    "AUTH",
    # 벤더 degraded 판정으로 호출하지 않음(게이트웨이). 원장 행 없음.
    "DEGRADED",
    # 사건 예산 초과로 호출하지 않음(게이트웨이). 원장 행 없음.
    "BUDGET",
]


@dataclass(frozen=True)
class Usage:
    """토큰 사용량. 벤더가 주지 않는 항목은 0."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True)
class Cost:
    """호출 비용. 벤더 응답에서 못 얻으면 `source="unknown"` 이고 값은 None."""

    ticks: int | None = None
    micro_usd: int | None = None
    source: CostSource = "unknown"


@dataclass(frozen=True)
class LLMResult:
    output: dict[str, Any] | None
    stop_reason: StopReason
    usage: Usage
    cost: Cost
    provider_request_id: str | None
    model_id: str
    vendor: str
    latency_ms: int


class LLMError(Exception):
    """모델 호출 실패. `kind` 로 분류한다(06 §3.1 표).

    `usage`·`cost` 는 벤더가 응답을 돌려준 실패(잘림·거절·파싱 실패)에서만 채운다.
    원장 FAILED + 사용량 기록용이다. timeout·연결 오류처럼 응답이 없으면 None.
    """

    def __init__(
        self,
        kind: LLMErrorKind,
        *,
        retry_after_s: float | None = None,
        message: str | None = None,
        usage: Usage | None = None,
        cost: Cost | None = None,
    ) -> None:
        super().__init__(message or kind)
        self.kind: LLMErrorKind = kind
        self.retry_after_s: float | None = retry_after_s
        self.usage: Usage | None = usage
        self.cost: Cost | None = cost


@runtime_checkable
class LLMPort(Protocol):
    async def structured_call(
        self,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> LLMResult: ...
