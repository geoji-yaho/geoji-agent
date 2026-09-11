"""호출 원장 포트 (01 §3.6). 구현은 작업 6(`ai.llm_calls`·`ai.node_results`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from geoji_ai.ports.llm import LLMError, LLMResult


@dataclass(frozen=True)
class CallSpec:
    """예약 단위. 필드는 06 §2 `CallSpec{node, call_index, vendor, model, est_max_micro_usd}`."""

    node: str
    call_index: int
    vendor: str
    model: str
    est_max_micro_usd: int


@runtime_checkable
class LedgerPort(Protocol):
    async def reserve(self, post_id: str, call: CallSpec) -> str: ...

    async def settle(self, call_id: str, result: LLMResult) -> None: ...

    async def mark_unknown(self, call_id: str, error: LLMError) -> None: ...

    async def get_node_result(
        self,
        request_hash: str,
        versions: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def put_node_result(
        self,
        call_id: str,
        request_hash: str,
        versions: dict[str, Any],
        output: dict[str, Any],
        expires_at: datetime,
    ) -> None: ...
