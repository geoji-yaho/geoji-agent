"""LLM 게이트웨이 — 원장·재시도·벤더 장애·`node_results` 재사용(06 §3.1·§3.2).

호출 한 번(`scoped_call`)의 순서:

1. `inner.route(role, model_override)` 로 (벤더, 모델) → 그 벤더가 degraded 면 호출·예약 없이
   `LLMError("DEGRADED")`
2. `node_results` 조회. 키 5요소(`NodeResultKey`)가 전부 같으면 inner 호출·예약 없이 저장된 출력을
   돌려준다
3. `est_max` 계산 → `ledger.reserve`. `BudgetExceeded` 면 호출 없이 `LLMError("BUDGET")`
4. inner 호출. 시도마다 `asyncio.wait_for(timeout)` 을 여기서 건다
5. 성공 → `settle` + `health.record_success`. 실패 → `health.record_failure`(카운트 대상 kind 는
   `VendorHealth` 가 거른다) → 재시도하지 않으면 `ledger_status` 대로 `fail`/`mark_unknown`
6. `backoff_seconds(... remaining_s ...)` 가 None 이 아니고 대기 뒤 남은 시간이 있으면 주입 `sleep`
   뒤 1회 재시도

정한 것(보고서 "계획서에 반영할 것"):

- 재시도는 **같은 원장 행**을 쓴다(코디네이터 9/14 결정 A). 첫 시도 실패 때 행을 닫지 않고 예약을
  유지했다가 마지막 시도 결과로 그 한 행을 settle/fail/mark_unknown 한다. `UNIQUE(generation_id,
  node, call_index)` 와 부딪히지 않는다
- `ledger.reserve` 가 `BudgetExceeded` 가 아닌 예외(같은 generation 재실행의 UNIQUE 충돌 등)를 내면
  원장을 건드리지 않고 `LLMError("TRANSPORT")` + 로그 `ledger_reserve_failed`
  (코디네이터 9/14 결정 2)
- `est_max` 입력 토큰은 메시지 content 문자열 길이 합 // 4.
  `adapters/fake_llm.py` `_rough_tokens` 와
  같은 규칙을 복제했다(application 은 어댑터를 import 하지 않는다). 정확 토크나이저 없음
- 단가표에 없는 모델은 `est_max = 0` 으로 예약하지 않는다. 예약·호출 없이 `LLMError("BUDGET")` +
  로그 `llm_price_missing`(설정 오류)
- inner 가 `LLMError` 가 아닌 예외를 내면 `TRANSPORT`(원장 UNKNOWN)로 보되 벤더 장애로 세지 않는다
- `node_results` 에는 **검증된 출력만** 넣는다. 게이트웨이는 검증을 모르므로 호출자(그래프)가 파싱·
  검증에 성공한 뒤 `remember(scoped)` 를 부른다. 조회·저장 오류는 로그만 남기고 호출을 막지 않는다

이 모듈은 SQLAlchemy·어댑터를 import 하지 않는다.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from geoji_ai.core.logging import get_logger
from geoji_ai.domain.budget import (
    NodeResultKey,
    budget_key_for_post,
    est_max_micro_usd,
    request_hash,
)
from geoji_ai.domain.intensity import ALL_INTENSITIES
from geoji_ai.domain.retries import backoff_seconds, ledger_status
from geoji_ai.domain.vendor_health import VendorHealth
from geoji_ai.ports.ledger import BudgetExceeded, CallSpec, LedgerPort
from geoji_ai.ports.llm import Cost, LLMError, LLMResult, LLMRole, Usage

if TYPE_CHECKING:
    from geoji_ai.contracts.case import CaseSnapshot

__all__ = [
    "EVALUATOR_SLOTS",
    "WRITER_SLOTS",
    "CallScope",
    "LLMGateway",
    "PriceLookup",
    "RoutedLLM",
    "ScopedLLM",
    "ScopedResult",
    "case_scope",
    "evaluator_call_index",
    "privacy_versions_json",
    "rough_prompt_tokens",
    "writer_call_index",
]

log = get_logger(__name__)

PriceLookup = Callable[[str], "tuple[float, float] | None"]

#: 서기 강도 슬롯 수. 보정 라운드마다 서기 `call_index` 가 이만큼 밀린다.
WRITER_SLOTS = len(ALL_INTENSITIES)
#: 검수 라운드 하나의 슬롯 수(기본 검수 + hell 별도 검수).
EVALUATOR_SLOTS = 2


# --- call_index(코디네이터 9/14 결정 3) ---------------------------------------------


def writer_call_index(repair_count: int, position: int) -> int:
    """서기 `call_index = repair_count × 3 + target_intensities 안 강도 순서`."""
    if repair_count < 0 or not 0 <= position < WRITER_SLOTS:
        raise ValueError(f"서기 call_index 범위 밖: repair_count={repair_count}, {position=}")
    return repair_count * WRITER_SLOTS + position


def evaluator_call_index(eval_round: int, hell_apart: bool) -> int:
    """검수 `call_index = 검수 라운드 × 2 + (hell 별도 검수면 1)`. 라운드는 0부터."""
    if eval_round < 0:
        raise ValueError(f"검수 라운드는 0 이상: {eval_round}")
    return eval_round * EVALUATOR_SLOTS + (1 if hell_apart else 0)


# --- 호출 문맥 ---------------------------------------------------------------------


@dataclass(frozen=True)
class CallScope:
    """호출 한 번의 원장·재사용 문맥.

    `remaining_s` 는 남은 시간을 돌려주는 함수(그래프 `Deadline.remaining_s`)다. 백오프
    대기 뒤에 다시 읽어야 해서 값이 아니라 함수로 받는다. None 이면 기한 없음(PREPARE).
    `reserve_s` 는 이 노드 뒤 필수 단계에 남길 시간이다.
    """

    budget_key: str
    node: str
    call_index: int
    generation_id: str | None
    job_id: str | None
    prompt_version: str
    policy_version: str
    privacy_versions: Any
    model_override: str | None = None
    remaining_s: Callable[[], float] | None = None
    reserve_s: float = 0.0

    def remaining(self) -> float:
        return math.inf if self.remaining_s is None else self.remaining_s()


def privacy_versions_json(items: Iterable[Any]) -> list[dict[str, Any]]:
    """스냅샷 `privacy_versions` → `node_results.privacy_versions` jsonb 값."""
    return [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in items
    ]


def case_scope(
    snapshot: CaseSnapshot,
    *,
    node: str,
    call_index: int,
    job_id: str | None,
    generation_id: str | None,
    prompt_version: str,
    policy_version: str,
    model_override: str | None = None,
    remaining_s: Callable[[], float] | None = None,
    reserve_s: float = 0.0,
) -> CallScope:
    """사건 스냅샷 기준 `CallScope`. 예산 키 `budget_key_for_post(post_id)`, 빈 id 는 None."""
    return CallScope(
        budget_key=budget_key_for_post(snapshot.post_id),
        node=node,
        call_index=call_index,
        generation_id=generation_id or None,
        job_id=job_id or None,
        prompt_version=prompt_version,
        policy_version=policy_version,
        privacy_versions=privacy_versions_json(snapshot.privacy_versions),
        model_override=model_override,
        remaining_s=remaining_s,
        reserve_s=reserve_s,
    )


def rough_prompt_tokens(messages: Iterable[dict]) -> int:
    """사전 입력 token 근사. `adapters/fake_llm.py` `_rough_tokens` 와 같은 규칙(len // 4)."""
    return len("".join(str(message.get("content", "")) for message in messages)) // 4


# --- 포트 모양 ---------------------------------------------------------------------


class RoutedLLM(Protocol):
    """게이트웨이의 inner. `adapters/llm_router.RoleRoutedLLM` 이 구조적으로 맞는다."""

    def route(self, role: LLMRole, model_override: str | None = None) -> tuple[str, str]: ...

    async def structured_call(
        self,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
        model_override: str | None = None,
    ) -> LLMResult: ...


@dataclass(frozen=True)
class ScopedResult:
    """`scoped_call` 결과. `remember` 에 그대로 돌려준다."""

    result: LLMResult
    reused: bool
    call_id: str | None
    request_hash: str
    versions: dict[str, Any]


@runtime_checkable
class ScopedLLM(Protocol):
    """그래프가 원장 경로를 고르는 기준. 없으면 기존 `LLMPort.structured_call` 경로다."""

    async def scoped_call(
        self,
        scope: CallScope,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> ScopedResult: ...

    async def remember(self, scoped: ScopedResult) -> None: ...


# --- 게이트웨이 --------------------------------------------------------------------


class LLMGateway:
    """`ScopedLLM` 구현. 워커 프로세스에 하나(`VendorHealth` 도 하나)."""

    def __init__(
        self,
        inner: RoutedLLM,
        ledger: LedgerPort,
        health: VendorHealth,
        price_lookup: PriceLookup,
        *,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._health = health
        self._price_lookup = price_lookup
        self._sleep = sleep

    async def scoped_call(
        self,
        scope: CallScope,
        *,
        role: LLMRole,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        max_output_tokens: int,
    ) -> ScopedResult:
        vendor, model_id = self._inner.route(role, scope.model_override)
        if self._health.is_degraded(vendor):
            log.warning("llm_vendor_degraded", vendor=vendor, node=scope.node)
            raise LLMError("DEGRADED", message=f"{vendor} degraded")

        rhash = request_hash(
            {
                "role": role,
                "model_id": model_id,
                "messages": messages,
                "schema": schema,
                "max_output_tokens": max_output_tokens,
            }
        )
        versions = NodeResultKey(
            rhash, model_id, scope.prompt_version, scope.policy_version, scope.privacy_versions
        ).versions()
        cached = await self._lookup(rhash, versions, scope)
        if cached is not None:
            log.info("llm_node_result_reused", node=scope.node, call_index=scope.call_index)
            return ScopedResult(
                _reused_result(cached, vendor, model_id), True, None, rhash, versions
            )

        price = self._price_lookup(model_id)
        if price is None:
            log.error("llm_price_missing", model_id=model_id, node=scope.node)
            raise LLMError("BUDGET", message=f"단가표에 없는 모델: {model_id}")
        est = est_max_micro_usd(rough_prompt_tokens(messages), max_output_tokens, *price)
        spec = CallSpec(
            node=scope.node,
            call_index=scope.call_index,
            vendor=vendor,
            model=model_id,
            est_max_micro_usd=est,
            request_hash=rhash,
            generation_id=scope.generation_id,
            job_id=scope.job_id,
        )
        try:
            call_id = await self._ledger.reserve(scope.budget_key, spec)
        except BudgetExceeded as exc:
            log.info("llm_budget_exceeded", node=scope.node, est_max_micro_usd=est)
            raise LLMError("BUDGET", message=str(exc)) from exc
        except Exception as exc:
            log.error(
                "ledger_reserve_failed",
                node=scope.node,
                call_index=scope.call_index,
                error=type(exc).__name__,
            )
            raise LLMError("TRANSPORT", message=f"원장 예약 실패: {type(exc).__name__}") from exc

        attempt = 1
        timeout = timeout_s
        while True:
            from_vendor = True
            try:
                result = await asyncio.wait_for(
                    self._inner.structured_call(
                        role=role,
                        messages=messages,
                        schema=schema,
                        timeout_s=timeout,
                        max_output_tokens=max_output_tokens,
                        model_override=scope.model_override,
                    ),
                    timeout,
                )
            except LLMError as exc:
                error = exc
            except TimeoutError:
                error = LLMError("TIMEOUT", message=f"{timeout}s 안에 응답이 없다")
            except Exception as exc:
                error = LLMError("TRANSPORT", message=f"inner 예외: {type(exc).__name__}")
                from_vendor = False
            else:
                await self._close("settle", call_id, result)
                self._health.record_success(vendor)
                return ScopedResult(result, False, call_id, rhash, versions)

            if from_vendor:
                self._health.record_failure(vendor, error.kind)
            remaining = scope.remaining()
            wait = backoff_seconds(
                error.kind,
                attempt,
                remaining,
                retry_after_s=error.retry_after_s,
                reserve_s=scope.reserve_s,
            )
            if wait is not None:
                retry_timeout = min(timeout_s, remaining - scope.reserve_s - wait)
                if retry_timeout > 0:
                    log.info("llm_retry", node=scope.node, kind=error.kind, wait_s=wait)
                    await self._sleep(wait)
                    attempt += 1
                    timeout = retry_timeout
                    continue
            if ledger_status(error.kind) == "UNKNOWN":
                await self._close("mark_unknown", call_id, error)
            else:
                await self._close("fail", call_id, error)
            raise error

    async def remember(self, scoped: ScopedResult) -> None:
        """검증을 통과한 출력을 `node_results` 에 넣는다. 재사용 hit·출력 없음은 넣지 않는다."""
        result = scoped.result
        if scoped.reused or scoped.call_id is None:
            return
        if result.output is None or result.stop_reason != "stop":
            return
        try:
            await self._ledger.put_node_result(
                scoped.call_id, scoped.request_hash, scoped.versions, result.output
            )
        except Exception as exc:
            log.warning("node_result_put_failed", error=type(exc).__name__)

    async def _lookup(
        self, rhash: str, versions: dict[str, Any], scope: CallScope
    ) -> dict[str, Any] | None:
        try:
            return await self._ledger.get_node_result(rhash, versions)
        except Exception as exc:
            log.warning("node_result_get_failed", node=scope.node, error=type(exc).__name__)
            return None

    async def _close(self, action: str, call_id: str, value: Any) -> None:
        try:
            await getattr(self._ledger, action)(call_id, value)
        except Exception as exc:
            log.error("ledger_close_failed", action=action, error=type(exc).__name__)


def _reused_result(output: dict[str, Any], vendor: str, model_id: str) -> LLMResult:
    """재사용 hit 의 결과. 새 호출이 아니므로 usage 0·비용 모름·latency 0·요청 id 없음(가짜 값)."""
    return LLMResult(
        output=dict(output),
        stop_reason="stop",
        usage=Usage(),
        cost=Cost(),
        provider_request_id=None,
        model_id=model_id,
        vendor=vendor,
        latency_ms=0,
    )
