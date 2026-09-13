"""노드 예산(05 §3.1, 스펙 케이스 ①~④)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from geoji_ai.domain.budget import Deadline, reserve_after

SETTINGS = SimpleNamespace(
    SENTENCING_NODE_TIMEOUT_SECONDS=3,
    WRITER_NODE_TIMEOUT_SECONDS=6,
    EVALUATOR_NODE_TIMEOUT_SECONDS=4,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _deadline(remaining: float, clock: FakeClock) -> Deadline:
    # 호스트 시각과 무관한 과거 DB 시각. 차이만 쓰여야 한다.
    db_now = datetime(2020, 1, 1, tzinfo=UTC)
    return Deadline.from_db(db_now + timedelta(seconds=remaining), db_now, clock=clock)


def test_from_db_uses_db_difference_not_host_clock() -> None:
    """① 호스트 시각이 틀려도(2020년 DB 시각) DB 차이로 남은 시간을 계산한다."""
    clock = FakeClock()
    deadline = _deadline(10, clock)
    assert deadline.remaining_s() == 10.0
    clock.now += 3
    assert deadline.remaining_s() == 7.0
    clock.now += 100
    assert deadline.remaining_s() == 0.0


@pytest.mark.parametrize(("remaining", "expected"), [(20, 6.0), (10.5, 6.0), (8, 3.5)])
def test_writer_timeout_is_min_of_cap_and_remaining_minus_reserve(
    remaining: float, expected: float
) -> None:
    """② writer = min(6, 남은 − 4.5)."""
    assert _deadline(remaining, FakeClock()).node_timeout("writer", SETTINGS) == expected


@pytest.mark.parametrize("remaining", [4.4, 4.5])
def test_writer_not_started_without_evaluator_time(remaining: float) -> None:
    """③ 남은 4.4s 에서 writer → None(검수 시간을 확보할 수 없으면 시작하지 않음)."""
    assert _deadline(remaining, FakeClock()).node_timeout("writer", SETTINGS) is None


@pytest.mark.parametrize(("remaining", "expected"), [(10, 4.0), (3, 2.5)])
def test_evaluator_timeout(remaining: float, expected: float) -> None:
    """④ evaluator = min(4, 남은 − 0.5)."""
    assert _deadline(remaining, FakeClock()).node_timeout("evaluator", SETTINGS) == expected


def test_evaluator_not_started_at_finalize_reserve() -> None:
    assert _deadline(0.5, FakeClock()).node_timeout("evaluator", SETTINGS) is None


@pytest.mark.parametrize(("remaining", "expected"), [(20, 3.0), (12, 1.5)])
def test_sentencing_reserves_writer_evaluator_finalize(remaining: float, expected: float) -> None:
    """sentencing 예약 = writer 상한 + evaluator 4s + finalize 0.5s(코디네이터 해석)."""
    assert reserve_after("sentencing", SETTINGS) == 10.5
    assert _deadline(remaining, FakeClock()).node_timeout("sentencing", SETTINGS) == expected


def test_unknown_node_rejected() -> None:
    with pytest.raises(ValueError):
        _deadline(10, FakeClock()).node_timeout("banter", SETTINGS)
