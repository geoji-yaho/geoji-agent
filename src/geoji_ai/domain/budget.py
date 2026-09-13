"""노드 시간 예산(05 §3.1).

DB 가 준 `deadline_at` 과 같은 DB 가 준 `db_now` 의 차이만 믿는다. 호스트 시계(`datetime.now`)
는 쓰지 않고, 그 차이를 로컬 `time.monotonic` 기준 만료 시점으로 바꾼다(proposal2 §14.1).

노드 timeout = `min(노드 상한, 남은 시간 − 다음 필수 단계 예약)`. 결과가 0 이하면 그 노드를
**시작하지 않는다**(`None`). 검수 시간을 확보할 수 없으면 새 서기 호출을 시작하지 않는 규칙이
이 계산으로 선다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "EVALUATOR",
    "FINALIZE_RESERVE_SECONDS",
    "NODE_NAMES",
    "SENTENCING",
    "WRITER",
    "Deadline",
    "node_cap",
    "reserve_after",
]

SENTENCING = "sentencing"
WRITER = "writer"
EVALUATOR = "evaluator"
NODE_NAMES: tuple[str, ...] = (SENTENCING, WRITER, EVALUATOR)

#: finalize 에 남겨 두는 시간(05 §3.1).
FINALIZE_RESERVE_SECONDS = 0.5

_CAP_SETTING: dict[str, str] = {
    SENTENCING: "SENTENCING_NODE_TIMEOUT_SECONDS",
    WRITER: "WRITER_NODE_TIMEOUT_SECONDS",
    EVALUATOR: "EVALUATOR_NODE_TIMEOUT_SECONDS",
}


def _check_name(name: str) -> str:
    if name not in _CAP_SETTING:
        raise ValueError(f"예산을 모르는 노드: {name!r}")
    return name


def node_cap(name: str, settings: Any) -> float:
    """노드 상한(설정값)."""
    return float(getattr(settings, _CAP_SETTING[_check_name(name)]))


def reserve_after(name: str, settings: Any) -> float:
    """이 노드 뒤 필수 단계에 남겨 둘 시간.

    - writer → evaluator 상한(4s) + finalize 0.5s
    - evaluator → finalize 0.5s
    - sentencing → writer 상한 + evaluator 상한 + finalize 0.5s(05 §3.1 에 없어 코디네이터 해석)
    """
    _check_name(name)
    if name == EVALUATOR:
        return FINALIZE_RESERVE_SECONDS
    after_writer = node_cap(EVALUATOR, settings) + FINALIZE_RESERVE_SECONDS
    if name == WRITER:
        return after_writer
    return node_cap(WRITER, settings) + after_writer


@dataclass(frozen=True)
class Deadline:
    """monotonic 기준 만료 시점. `clock` 은 테스트가 바꿔 끼운다."""

    expires_at_monotonic: float
    clock: Callable[[], float] = field(default=time.monotonic, compare=False, repr=False)

    @classmethod
    def from_db(
        cls,
        deadline_at: datetime,
        db_now: datetime,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> Deadline:
        """`deadline_at − db_now` 를 지금의 monotonic 에 더한다. 두 시각 모두 DB 가 준 값이다."""
        remaining = (deadline_at - db_now).total_seconds()
        return cls(expires_at_monotonic=clock() + remaining, clock=clock)

    def remaining_s(self) -> float:
        """남은 초. 지났으면 0."""
        return max(0.0, self.expires_at_monotonic - self.clock())

    def node_timeout(self, name: str, settings: Any) -> float | None:
        """`min(노드 상한, 남은 − reserve_after(name))`. 0 이하면 `None`(시작하지 않음)."""
        budget = min(node_cap(name, settings), self.remaining_s() - reserve_after(name, settings))
        if budget <= 0:
            return None
        return budget
