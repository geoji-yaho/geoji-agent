"""`LedgerPort` 의 Postgres 구현(06 §3.2, DDL 003).

- `reserve` 는 한 트랜잭션이다. `case_budgets` 행을 없으면 만들고 `FOR UPDATE` 로 잠근 뒤
  `spent + reserved + est_max > cap` 이면 `BudgetExceeded`(롤백 — `llm_calls` 행 없음, reserved
  불변). 넘지 않으면 `reserved += est_max` 와 `llm_calls` INSERT(`RESERVED`). `UNIQUE(generation_id,
  node, call_index)` 위반은 `IntegrityError` 로 올라가고 트랜잭션 전체가 롤백돼 reserved 도 원복된다
- `settle`·`fail`·`mark_unknown` 은 `status IN ('RESERVED','SENT')` 인 행만 바꾼다. 이미 정산된 행에
  두 번 오면 아무것도 하지 않는다(이중 정산 방지)
- 정산액은 `cost.micro_usd` → 없으면 `cost.ticks` 내림 환산 → 둘 다 없으면 **비용 모름**. 비용을
  모르면 보수적으로 `est_max` 를 spent 에 더하고 `actual_micro_usd`·`cost_ticks` 는 NULL 로 둔다.
  DDL 에 `cost_source` 컬럼이 없어 이 둘이 NULL 인 것이 "비용 모름" 표시다
- `fail` 은 `error.usage`·`error.cost` 가 모두 None(응답 없음)이면 spent 에 0 을 더한다
- `mark_unknown` 은 예약을 유지하고 spent 를 건드리지 않는다. 정리는 작업 8
- `node_results` 는 5요소 전부 일치 ∧ 만료 전 ∧ `invalidated_at IS NULL` 일 때만 돌려준다
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from geoji_ai.core.logging import get_logger
from geoji_ai.domain.budget import (
    CASE_CAP_MICRO_USD,
    NODE_RESULT_TTL,
    node_result_expires_at,
    resolve_actual_micro_usd,
)
from geoji_ai.ports.ledger import BudgetExceeded, CallSpec
from geoji_ai.ports.llm import Cost, LLMError, LLMResult, Usage

__all__ = ["VERSION_KEYS", "PostgresCallLedger"]

log = get_logger(__name__)

#: `versions` 인자에 있어야 하는 4요소(`NodeResultKey.versions()`).
VERSION_KEYS: tuple[str, ...] = ("model_id", "prompt_version", "policy_version", "privacy_versions")

_OPEN_STATUSES = "('RESERVED','SENT')"

_ENSURE_BUDGET_SQL = text(
    """
    INSERT INTO ai.case_budgets (post_id, cap_micro_usd)
    VALUES (:post_id, :cap)
    ON CONFLICT (post_id) DO NOTHING
    """
)

_LOCK_BUDGET_SQL = text(
    """
    SELECT cap_micro_usd, spent_micro_usd, reserved_micro_usd
    FROM ai.case_budgets
    WHERE post_id = :post_id
    FOR UPDATE
    """
)

_ADD_RESERVED_SQL = text(
    """
    UPDATE ai.case_budgets
    SET reserved_micro_usd = reserved_micro_usd + :est
    WHERE post_id = :post_id
    """
)

_INSERT_CALL_SQL = text(
    """
    INSERT INTO ai.llm_calls (
        id, post_id, job_id, generation_id, node, call_index,
        vendor, model_id, request_hash, status, estimated_max_micro_usd, started_at
    ) VALUES (
        CAST(:id AS uuid), :post_id, CAST(:job_id AS uuid), CAST(:generation_id AS uuid),
        :node, :call_index, :vendor, :model_id, :request_hash, 'RESERVED', :est, now()
    )
    """
)

_CLOSE_CALL_SQL = text(
    f"""
    UPDATE ai.llm_calls
    SET status = :status,
        actual_micro_usd = :actual,
        cost_ticks = :ticks,
        prompt_tokens = COALESCE(:prompt_tokens, prompt_tokens),
        completion_tokens = COALESCE(:completion_tokens, completion_tokens),
        reasoning_tokens = COALESCE(:reasoning_tokens, reasoning_tokens),
        cached_tokens = COALESCE(:cached_tokens, cached_tokens),
        provider_request_id = COALESCE(:provider_request_id, provider_request_id),
        finished_at = now()
    WHERE id = CAST(:id AS uuid) AND status IN {_OPEN_STATUSES}
    RETURNING post_id, estimated_max_micro_usd
    """
)

_SETTLE_BUDGET_SQL = text(
    """
    UPDATE ai.case_budgets
    SET spent_micro_usd = spent_micro_usd + :charge,
        reserved_micro_usd = reserved_micro_usd - :est
    WHERE post_id = :post_id
    """
)

_MARK_UNKNOWN_SQL = text(
    f"""
    UPDATE ai.llm_calls
    SET status = 'UNKNOWN', finished_at = now()
    WHERE id = CAST(:id AS uuid) AND status IN {_OPEN_STATUSES}
    """
)

_GET_NODE_RESULT_SQL = text(
    """
    SELECT validated_output
    FROM ai.node_results
    WHERE request_hash = :request_hash
      AND model_id = :model_id
      AND prompt_version = :prompt_version
      AND policy_version = :policy_version
      AND privacy_versions = CAST(:privacy_versions AS jsonb)
      AND invalidated_at IS NULL
      AND expires_at > COALESCE(CAST(:now AS timestamptz), now())
    ORDER BY created_at DESC
    LIMIT 1
    """
)

_PUT_NODE_RESULT_SQL = text(
    """
    INSERT INTO ai.node_results (
        call_id, request_hash, model_id, prompt_version, policy_version,
        privacy_versions, validated_output, created_at, expires_at
    ) VALUES (
        CAST(:call_id AS uuid), :request_hash, :model_id, :prompt_version, :policy_version,
        CAST(:privacy_versions AS jsonb), CAST(:output AS jsonb), now(),
        COALESCE(CAST(:expires_at AS timestamptz), now() + make_interval(secs => :ttl_s))
    )
    ON CONFLICT (call_id) DO NOTHING
    """
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _require_versions(versions: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in VERSION_KEYS if key not in versions]
    if missing:
        raise ValueError(f"versions 에 빠진 요소: {missing}")
    return {
        "model_id": str(versions["model_id"]),
        "prompt_version": str(versions["prompt_version"]),
        "policy_version": str(versions["policy_version"]),
        "privacy_versions": _json(versions["privacy_versions"]),
    }


def _usage_params(usage: Usage | None) -> dict[str, int | None]:
    if usage is None:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "cached_tokens": None,
        }
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cached_tokens": usage.cached_tokens,
    }


class PostgresCallLedger:
    """`ai.case_budgets`·`ai.llm_calls`·`ai.node_results` 원장.

    `clock` 은 `node_results` 의 만료 기준 시각이다. None 이면 DB `now()` 를 쓴다.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        cap_micro_usd: int = CASE_CAP_MICRO_USD,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._cap = cap_micro_usd
        self._clock = clock

    # --- 예산 ----------------------------------------------------------------------

    async def reserve(self, post_id: str, call: CallSpec) -> str:
        call_id = str(uuid.uuid4())
        async with self._engine.begin() as conn:
            await conn.execute(_ENSURE_BUDGET_SQL, {"post_id": post_id, "cap": self._cap})
            row = (await conn.execute(_LOCK_BUDGET_SQL, {"post_id": post_id})).mappings().one()
            cap = int(row["cap_micro_usd"])
            spent = int(row["spent_micro_usd"])
            reserved = int(row["reserved_micro_usd"])
            if spent + reserved + call.est_max_micro_usd > cap:
                raise BudgetExceeded(
                    post_id,
                    cap_micro_usd=cap,
                    spent_micro_usd=spent,
                    reserved_micro_usd=reserved,
                    est_max_micro_usd=call.est_max_micro_usd,
                )
            await conn.execute(
                _ADD_RESERVED_SQL, {"post_id": post_id, "est": call.est_max_micro_usd}
            )
            await conn.execute(
                _INSERT_CALL_SQL,
                {
                    "id": call_id,
                    "post_id": post_id,
                    "job_id": call.job_id,
                    "generation_id": call.generation_id,
                    "node": call.node,
                    "call_index": call.call_index,
                    "vendor": call.vendor,
                    "model_id": call.model,
                    "request_hash": call.request_hash,
                    "est": call.est_max_micro_usd,
                },
            )
        return call_id

    async def settle(self, call_id: str, result: LLMResult) -> None:
        """COMPLETE. 비용을 모르면 `est_max` 를 spent 에 더한다(모듈 docstring)."""
        async with self._engine.begin() as conn:
            await self._close(
                conn,
                call_id,
                status="COMPLETE",
                usage=result.usage,
                cost=result.cost,
                provider_request_id=result.provider_request_id,
            )

    async def fail(self, call_id: str, error: LLMError) -> None:
        """FAILED. 응답이 없던 실패(usage·cost 모두 None)는 spent 0, 예약은 해제한다."""
        usage: Usage | None = getattr(error, "usage", None)
        cost: Cost | None = getattr(error, "cost", None)
        async with self._engine.begin() as conn:
            await self._close(
                conn,
                call_id,
                status="FAILED",
                usage=usage,
                cost=cost,
                provider_request_id=None,
                no_response=usage is None and cost is None,
            )

    async def mark_unknown(self, call_id: str, error: LLMError) -> None:
        """UNKNOWN. 예약 유지, spent 불변. 정리는 작업 8(`ledger-sweep`)."""
        async with self._engine.begin() as conn:
            result = await conn.execute(_MARK_UNKNOWN_SQL, {"id": call_id})
        if result.rowcount == 0:
            log.warning("ledger_mark_unknown_skipped", call_id=call_id, kind=error.kind)

    async def _close(
        self,
        conn: AsyncConnection,
        call_id: str,
        *,
        status: str,
        usage: Usage | None,
        cost: Cost | None,
        provider_request_id: str | None,
        no_response: bool = False,
    ) -> None:
        ticks = cost.ticks if cost is not None else None
        micro = cost.micro_usd if cost is not None else None
        actual = resolve_actual_micro_usd(micro, ticks)
        row = (
            (
                await conn.execute(
                    _CLOSE_CALL_SQL,
                    {
                        "id": call_id,
                        "status": status,
                        "actual": actual,
                        "ticks": ticks,
                        "provider_request_id": provider_request_id,
                        **_usage_params(usage),
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            log.warning("ledger_close_skipped", call_id=call_id, status=status)
            return
        est = int(row["estimated_max_micro_usd"])
        if no_response:
            charge = 0
        elif actual is None:
            charge = est
        else:
            charge = actual
        await conn.execute(
            _SETTLE_BUDGET_SQL, {"post_id": row["post_id"], "charge": charge, "est": est}
        )

    # --- node_results --------------------------------------------------------------

    async def get_node_result(
        self,
        request_hash: str,
        versions: dict[str, Any],
    ) -> dict[str, Any] | None:
        params = {
            "request_hash": request_hash,
            "now": self._clock() if self._clock is not None else None,
            **_require_versions(versions),
        }
        async with self._engine.connect() as conn:
            value = (await conn.execute(_GET_NODE_RESULT_SQL, params)).scalar_one_or_none()
        if value is None:
            return None
        if isinstance(value, str | bytes):
            return json.loads(value)
        return dict(value)

    async def put_node_result(
        self,
        call_id: str,
        request_hash: str,
        versions: dict[str, Any],
        output: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> None:
        """검증된 출력만 넣는다(원문 프롬프트·자유형 응답 금지). 같은 `call_id` 두 번은 무시."""
        if expires_at is None and self._clock is not None:
            expires_at = node_result_expires_at(self._clock())
        params = {
            "call_id": call_id,
            "request_hash": request_hash,
            "output": _json(output),
            "expires_at": expires_at,
            "ttl_s": NODE_RESULT_TTL.total_seconds(),
            **_require_versions(versions),
        }
        async with self._engine.begin() as conn:
            await conn.execute(_PUT_NODE_RESULT_SQL, params)
