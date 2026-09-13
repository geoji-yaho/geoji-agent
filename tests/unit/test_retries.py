"""오류 코드 → 폴백 분기(10 §4.6, 스펙 케이스 ⑭)."""

from __future__ import annotations

import pytest

from geoji_ai.domain.retries import FallbackAction, classify_generation_error


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
