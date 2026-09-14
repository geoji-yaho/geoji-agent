"""생성 실패 → 폴백 분기(05 §3.6, 03 §1 = 10 §4.6)와 벤더 오류 분류·백오프(06 §3.1).

`generation-failed` 의 `error_code` 가 백엔드에서 TEXT_RETRY round 를 예약하는지 가르고,
`LLMError.kind` 를 생성 오류 코드·원장 상태·재시도 여부·대기 시간으로 옮긴다.
순수 함수만 둔다. 배선은 `application/llm_gateway.py`.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

__all__ = [
    "NO_LEDGER_KINDS",
    "NO_RETRY_CODES",
    "RETRYABLE_KINDS",
    "TEXT_RETRY_CODES",
    "FallbackAction",
    "LedgerStatus",
    "backoff_seconds",
    "classify_generation_error",
    "classify_vendor_error",
    "ledger_status",
    "retry_allowed",
    "writer_failure_code",
]


class FallbackAction(StrEnum):
    #: 즉시 폴백(형량 fallback_sentence FINAL/RULE, 문구 TEMPLATE). TEXT_RETRY 예약 안 함.
    NO_RETRY = "NO_RETRY"
    #: 같은 폴백 + TEXT_RETRY round 1 예약.
    SCHEDULE_TEXT_RETRY = "SCHEDULE_TEXT_RETRY"


#: 10 §4.6 표 1행.
NO_RETRY_CODES: frozenset[str] = frozenset({"AI_NOT_READY", "POLICY_ERROR", "EVIDENCE_INVALIDATED"})

#: 10 §4.6 표 2행.
TEXT_RETRY_CODES: frozenset[str] = frozenset(
    {"VENDOR_UNAVAILABLE", "BUDGET_EXCEEDED", "EVAL_FAILED", "SCHEMA_INVALID", "DEADLINE_EXCEEDED"}
)

LedgerStatus = Literal["FAILED", "UNKNOWN"]

#: `LLMError.kind` → 생성 오류 코드(06 §3.1, 코디네이터 해석 9/14).
_VENDOR_ERROR_CODE: dict[str, str] = {
    "SCHEMA": "SCHEMA_INVALID",
    "PARSE": "SCHEMA_INVALID",
    "REFUSAL": "VENDOR_UNAVAILABLE",
    "RATE_LIMIT": "VENDOR_UNAVAILABLE",
    "SERVER": "VENDOR_UNAVAILABLE",
    # 상한 안에 시작해 시간 안에 끝나지 못한 호출은 시간 예산 실패다
    # (08 §3.5 `WRITER_NODE_TIMEOUT_SECONDS=0.1` → `DEADLINE_EXCEEDED`, 10 §4.6).
    # 연결 실패(TRANSPORT)는 벤더 쪽으로 둔다.
    "TIMEOUT": "DEADLINE_EXCEEDED",
    "TRANSPORT": "VENDOR_UNAVAILABLE",
    "AUTH": "VENDOR_UNAVAILABLE",
    "DEGRADED": "VENDOR_UNAVAILABLE",
    "BUDGET": "BUDGET_EXCEEDED",
}

#: 호출을 시작하지 않아 원장 행이 없는 kind. 원장 상태가 없다(`ledger_status` 는 `ValueError`).
NO_LEDGER_KINDS: frozenset[str] = frozenset({"DEGRADED", "BUDGET"})

#: 청구 여부가 불명확한 실패. 원장은 UNKNOWN 으로 두고 예약을 유지한다(06 §3.1).
_UNKNOWN_LEDGER_KINDS: frozenset[str] = frozenset({"TIMEOUT", "TRANSPORT"})

#: 1회 재시도가 허용되는 kind(06 §3.1 429·5xx 행).
RETRYABLE_KINDS: frozenset[str] = frozenset({"RATE_LIMIT", "SERVER"})

#: 재시도 허용 횟수. 첫 시도 실패 뒤 1회.
_MAX_RETRY_ATTEMPT = 1


def classify_generation_error(code: str) -> FallbackAction:
    """오류 코드 → 폴백 분기. 표에 없는 코드는 `ValueError`."""
    if code in NO_RETRY_CODES:
        return FallbackAction.NO_RETRY
    if code in TEXT_RETRY_CODES:
        return FallbackAction.SCHEDULE_TEXT_RETRY
    raise ValueError(f"오류 코드 표(10 §4.6)에 없는 코드: {code!r}")


def classify_vendor_error(kind: str) -> str:
    """`LLMError.kind` → 생성 오류 코드. 모르는 kind 는 `ValueError`."""
    try:
        return _VENDOR_ERROR_CODE[kind]
    except KeyError:
        raise ValueError(f"벤더 오류 분류 표(06 §3.1)에 없는 kind: {kind!r}") from None


def writer_failure_code(errors: Iterable[str | None], *, budget_skipped: bool) -> str:
    """서기가 AI 문구를 하나도 못 냈을 때의 생성 오류 코드(08 §3.5, 10 §4.6).

    우선순위 예산 > 시간 > 벤더. 오류에 `BUDGET` 이 있으면 `BUDGET_EXCEEDED`, 시간 예산으로 시작하지
    못한 강도가 있거나(`budget_skipped`) 분류가 `DEADLINE_EXCEEDED` 인 오류(TIMEOUT)가 있으면
    `DEADLINE_EXCEEDED`, 그 밖(DEGRADED·AUTH·SERVER·검증 실패·모르는 kind·None)은
    `VENDOR_UNAVAILABLE`.
    """
    kinds = {kind for kind in errors if kind is not None}
    if "BUDGET" in kinds:
        return "BUDGET_EXCEEDED"
    if budget_skipped or any(_VENDOR_ERROR_CODE.get(kind) == "DEADLINE_EXCEEDED" for kind in kinds):
        return "DEADLINE_EXCEEDED"
    return "VENDOR_UNAVAILABLE"


def ledger_status(kind: str) -> LedgerStatus:
    """`LLMError.kind` → 원장 상태. TIMEOUT·TRANSPORT 는 UNKNOWN, 나머지는 FAILED.

    `DEGRADED`·`BUDGET` 은 호출 전에 끝나 원장 행이 없다. 상태를 물으면 `ValueError`.
    """
    if kind not in _VENDOR_ERROR_CODE:
        raise ValueError(f"벤더 오류 분류 표(06 §3.1)에 없는 kind: {kind!r}")
    if kind in NO_LEDGER_KINDS:
        raise ValueError(f"원장 행이 없는 kind: {kind!r}")
    return "UNKNOWN" if kind in _UNKNOWN_LEDGER_KINDS else "FAILED"


def retry_allowed(kind: str, attempt: int) -> bool:
    """방금 실패한 시도 번호 `attempt`(1부터) 뒤에 재시도할 수 있는가.

    RATE_LIMIT·SERVER 만, 첫 시도가 실패한 뒤 1회.
    """
    return kind in RETRYABLE_KINDS and attempt == _MAX_RETRY_ATTEMPT


def backoff_seconds(
    kind: str,
    attempt: int,
    remaining_s: float,
    *,
    retry_after_s: float | None = None,
    reserve_s: float = 0.0,
) -> float | None:
    """재시도 전 대기 초. `min(Retry-After, 남은 − 예약)`.

    재시도할 수 없거나 Retry-After 가 남은 시간(− 예약) 밖이면 None(즉시 폴백).
    Retry-After 가 없으면 0초로 본다(계획서에 기본 대기값이 없다. 코디네이터 해석 9/14).
    """
    if not retry_allowed(kind, attempt):
        return None
    wait = max(retry_after_s or 0.0, 0.0)
    budget = remaining_s - reserve_s
    if budget < 0 or wait > budget:
        return None
    return min(wait, budget)
