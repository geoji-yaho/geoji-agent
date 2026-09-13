"""호출 원장 포트 (01 §3.6, 06 §3.2). 구현은 `adapters/postgres_call_ledger.py`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from geoji_ai.ports.llm import LLMError, LLMResult

__all__ = ["BudgetExceeded", "CallSpec", "LedgerPort"]


@dataclass(frozen=True)
class CallSpec:
    """예약 단위. 06 §2 `CallSpec{node, call_index, vendor, model, est_max_micro_usd}` 에
    `request_hash`(필수)·`generation_id`·`job_id` 를 더했다(`ai.llm_calls` 컬럼).
    """

    node: str
    call_index: int
    vendor: str
    model: str
    est_max_micro_usd: int
    request_hash: str
    generation_id: str | None = None
    job_id: str | None = None


class BudgetExceeded(Exception):
    """`spent + reserved + est_max > cap`. 호출을 시작하지 않는다(06 §3.2 `BUDGET_EXCEEDED`)."""

    def __init__(
        self,
        budget_key: str,
        *,
        cap_micro_usd: int,
        spent_micro_usd: int,
        reserved_micro_usd: int,
        est_max_micro_usd: int,
    ) -> None:
        super().__init__(
            f"예산 초과: spent={spent_micro_usd} reserved={reserved_micro_usd} "
            f"est_max={est_max_micro_usd} cap={cap_micro_usd}"
        )
        self.budget_key = budget_key
        self.cap_micro_usd = cap_micro_usd
        self.spent_micro_usd = spent_micro_usd
        self.reserved_micro_usd = reserved_micro_usd
        self.est_max_micro_usd = est_max_micro_usd


@runtime_checkable
class LedgerPort(Protocol):
    async def reserve(self, post_id: str, call: CallSpec) -> str:
        """예약. `post_id` 자리에는 `budget_key_for_post/submission` 결과가 온다.
        cap 을 넘으면 `BudgetExceeded`."""
        ...

    async def settle(self, call_id: str, result: LLMResult) -> None: ...

    async def fail(self, call_id: str, error: LLMError) -> None:
        """FAILED. 사용량이 있으면 그만큼 정산하고 예약은 해제한다."""
        ...

    async def mark_unknown(self, call_id: str, error: LLMError) -> None: ...

    async def get_node_result(
        self,
        request_hash: str,
        versions: dict[str, Any],
    ) -> dict[str, Any] | None:
        """`versions` = `NodeResultKey.versions()` 4요소."""
        ...

    async def put_node_result(
        self,
        call_id: str,
        request_hash: str,
        versions: dict[str, Any],
        output: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> None:
        """검증된 출력만 넣는다. `expires_at` 이 None 이면 24h 뒤."""
        ...
