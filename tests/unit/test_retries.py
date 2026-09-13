"""오류 코드 → 폴백 분기(10 §4.6, 05 스펙 케이스 ⑭)와 벤더 오류 분류·백오프(06 §3.1)."""

from __future__ import annotations

import pytest

from geoji_ai.domain.retries import (
    NO_LEDGER_KINDS,
    FallbackAction,
    backoff_seconds,
    classify_generation_error,
    classify_vendor_error,
    ledger_status,
    retry_allowed,
)


@pytest.mark.parametrize(
    ("code", "action"),
    [
        ("AI_NOT_READY", FallbackAction.NO_RETRY),
        ("POLICY_ERROR", FallbackAction.NO_RETRY),
        ("EVIDENCE_INVALIDATED", FallbackAction.NO_RETRY),
        ("VENDOR_UNAVAILABLE", FallbackAction.SCHEDULE_TEXT_RETRY),
        ("BUDGET_EXCEEDED", FallbackAction.SCHEDULE_TEXT_RETRY),
        ("EVAL_FAILED", FallbackAction.SCHEDULE_TEXT_RETRY),
        ("SCHEMA_INVALID", FallbackAction.SCHEDULE_TEXT_RETRY),
        ("DEADLINE_EXCEEDED", FallbackAction.SCHEDULE_TEXT_RETRY),
    ],
)
def test_eight_codes_split_into_two_branches(code: str, action: FallbackAction) -> None:
    """⑭ 오류 코드 8종 → 두 분기."""
    assert classify_generation_error(code) is action


def test_unknown_code_rejected() -> None:
    with pytest.raises(ValueError):
        classify_generation_error("STALE_GENERATION")


# 06 ⑧ 6상황(kind 7종) → 생성 오류 코드·원장 상태
@pytest.mark.parametrize(
    ("kind", "code", "status"),
    [
        ("SCHEMA", "SCHEMA_INVALID", "FAILED"),
        ("PARSE", "SCHEMA_INVALID", "FAILED"),
        ("REFUSAL", "VENDOR_UNAVAILABLE", "FAILED"),
        ("RATE_LIMIT", "VENDOR_UNAVAILABLE", "FAILED"),
        ("SERVER", "VENDOR_UNAVAILABLE", "FAILED"),
        ("TIMEOUT", "VENDOR_UNAVAILABLE", "UNKNOWN"),
        ("TRANSPORT", "VENDOR_UNAVAILABLE", "UNKNOWN"),
    ],
)
def test_vendor_error_code_and_ledger_status(kind: str, code: str, status: str) -> None:
    """06 ⑧ kind → 코드·원장 상태 표."""
    assert classify_vendor_error(kind) == code
    assert ledger_status(kind) == status
    # 생성 오류 코드는 모두 10 §4.6 표 안이다
    assert classify_generation_error(code) is FallbackAction.SCHEDULE_TEXT_RETRY


def test_unknown_vendor_kind_rejected() -> None:
    with pytest.raises(ValueError):
        classify_vendor_error("BOOM")
    with pytest.raises(ValueError):
        ledger_status("BOOM")


@pytest.mark.parametrize(
    ("kind", "allowed"),
    [
        ("RATE_LIMIT", True),
        ("SERVER", True),
        ("REFUSAL", False),
        ("TIMEOUT", False),
        ("TRANSPORT", False),
        ("SCHEMA", False),
        ("PARSE", False),
    ],
)
def test_retry_allowed_only_rate_limit_and_server(kind: str, allowed: bool) -> None:
    assert retry_allowed(kind, 1) is allowed


def test_429_backoff_is_min_retry_after_and_remaining_minus_reserve() -> None:
    """06 ⑨ 429 backoff = min(retry_after, 남은 − 예약)."""
    assert backoff_seconds("RATE_LIMIT", 1, 5.0, retry_after_s=2.0, reserve_s=1.5) == 2.0
    # 남은 − 예약이 딱 retry_after 면 그 안이다
    assert backoff_seconds("RATE_LIMIT", 1, 4.0, retry_after_s=2.0, reserve_s=2.0) == 2.0


def test_backoff_without_retry_after_is_zero() -> None:
    """Retry-After 없음 → 0초(코디네이터 해석 9/14). 기존 위치 인자 3개로도 부른다."""
    assert backoff_seconds("RATE_LIMIT", 1, 3.0) == 0.0
    assert backoff_seconds("SERVER", 1, 3.0, reserve_s=1.0) == 0.0


def test_backoff_beyond_remaining_is_none() -> None:
    """06 ⑩ 남은 − 예약 < retry_after → None(즉시 폴백)."""
    assert backoff_seconds("RATE_LIMIT", 1, 3.0, retry_after_s=2.0, reserve_s=1.5) is None
    assert backoff_seconds("SERVER", 1, 1.0, retry_after_s=None, reserve_s=2.0) is None


def test_second_attempt_cannot_retry() -> None:
    """06 ⑪ attempt 2 → 재시도 불가."""
    assert retry_allowed("RATE_LIMIT", 2) is False
    assert retry_allowed("SERVER", 2) is False
    assert backoff_seconds("RATE_LIMIT", 2, 10.0, retry_after_s=1.0) is None


def test_non_retryable_kind_backoff_is_none() -> None:
    assert backoff_seconds("TIMEOUT", 1, 10.0) is None
    assert backoff_seconds("REFUSAL", 1, 10.0) is None


# 06 §3.1 추가 kind(9/14): AUTH·DEGRADED·BUDGET
@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("AUTH", "VENDOR_UNAVAILABLE"),
        ("DEGRADED", "VENDOR_UNAVAILABLE"),
        ("BUDGET", "BUDGET_EXCEEDED"),
    ],
)
def test_added_kinds_map_to_generation_codes(kind: str, code: str) -> None:
    assert classify_vendor_error(kind) == code
    assert classify_generation_error(code) is FallbackAction.SCHEDULE_TEXT_RETRY
    assert retry_allowed(kind, 1) is False
    assert backoff_seconds(kind, 1, 10.0) is None


def test_auth_ledger_status_is_failed() -> None:
    assert ledger_status("AUTH") == "FAILED"


@pytest.mark.parametrize("kind", ["DEGRADED", "BUDGET"])
def test_no_ledger_row_kinds_have_no_ledger_status(kind: str) -> None:
    """호출 전에 끝나 원장 행이 없다 → 상태를 물으면 ValueError."""
    assert kind in NO_LEDGER_KINDS
    with pytest.raises(ValueError):
        ledger_status(kind)
