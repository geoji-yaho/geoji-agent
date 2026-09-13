"""벤더 장애 판정(06 §3.1 마지막 행).

같은 벤더에서 `SERVER`/`TRANSPORT` 가 연속 3회, 60초 창 안에 나면 그 벤더를 degraded 로 본다.
상태는 프로세스 메모리에만 둔다.

계획서에 없어 코디네이터가 정한 해석(9/14):
- 사이에 성공이 한 번 있으면 카운트를 0 으로 되돌린다
- `RATE_LIMIT`·`SCHEMA` 등 다른 kind 는 카운트에 넣지도 끊지도 않는다
- 마지막 실패 뒤 60초가 지나거나 성공 1회면 해제
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

__all__ = ["DEGRADED_KINDS", "DEGRADED_THRESHOLD", "DEGRADED_WINDOW_S", "VendorHealth"]

DEGRADED_THRESHOLD = 3
DEGRADED_WINDOW_S = 60.0
DEGRADED_KINDS: frozenset[str] = frozenset({"SERVER", "TRANSPORT"})


class VendorHealth:
    """벤더별 연속 장애 실패 시각을 들고 degraded 여부를 판정한다."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}

    def record_failure(self, vendor: str, kind: str) -> None:
        """실패 1건. `SERVER`/`TRANSPORT` 만 센다. 다른 kind 는 무시한다."""
        if kind not in DEGRADED_KINDS:
            return
        times = self._failures.setdefault(vendor, deque(maxlen=DEGRADED_THRESHOLD))
        times.append(self._clock())

    def record_success(self, vendor: str) -> None:
        """성공 1건. 연속 실패를 끊는다."""
        self._failures.pop(vendor, None)

    def is_degraded(self, vendor: str) -> bool:
        times = self._failures.get(vendor)
        if not times or len(times) < DEGRADED_THRESHOLD:
            return False
        if self._clock() - times[-1] > DEGRADED_WINDOW_S:
            return False
        return times[-1] - times[0] <= DEGRADED_WINDOW_S
