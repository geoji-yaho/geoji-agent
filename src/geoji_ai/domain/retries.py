"""생성 실패 → 폴백 분기 골격(05 §3.6, 03 §1 = 10 §4.6).

`generation-failed` 의 `error_code` 가 백엔드에서 TEXT_RETRY round 를 예약하는지만 가른다.
벤더 오류 분류(429·5xx·timeout)와 백오프는 작업 6 몫이라 자리만 둔다.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "NO_RETRY_CODES",
    "TEXT_RETRY_CODES",
    "FallbackAction",
    "backoff_seconds",
    "classify_generation_error",
    "classify_vendor_error",
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


def classify_generation_error(code: str) -> FallbackAction:
    """오류 코드 → 폴백 분기. 표에 없는 코드는 `ValueError`."""
    if code in NO_RETRY_CODES:
        return FallbackAction.NO_RETRY
    if code in TEXT_RETRY_CODES:
        return FallbackAction.SCHEDULE_TEXT_RETRY
    raise ValueError(f"오류 코드 표(10 §4.6)에 없는 코드: {code!r}")


def classify_vendor_error(kind: str) -> str:
    """`LLMError.kind` → 생성 오류 코드. 작업 6 에서 채운다."""
    raise NotImplementedError("벤더 오류 분류는 작업 6(06) 몫이다")


def backoff_seconds(kind: str, attempt: int, remaining_s: float) -> float | None:
    """429 백오프. 남은 시간을 넘으면 None(즉시 폴백). 작업 6 에서 채운다."""
    raise NotImplementedError("백오프는 작업 6(06) 몫이다")
