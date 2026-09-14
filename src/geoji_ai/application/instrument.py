"""계측 호출 헬퍼(08 §3.3 로그·지표·알림).

계측은 동작을 바꾸지 않는다. 지표 기록·노드 로그·알림·비용 집계가 어떤 예외를 내도 여기서
삼키고 `instrument_failed` 로그(지표 이름·예외 타입만)만 남긴다. 그 로그마저 실패하면 조용히
넘어간다.

- 지표는 `telemetry.metrics.REGISTRY` 를 **호출 시점에** 읽는다(테스트가 바꿔 끼운다).
  이름·라벨은 `METRICS` 정의를 그대로 따른다. 라벨에 개별 ID 를 넣지 않는다.
- 노드 로그는 `telemetry.logs.log_node`. 필드는 `LOG_FIELDS` 만 넘긴다(원문 없음).
- 알림·비용 경고는 주입된 `AlertNotifier`·`DailyCostTracker` 가 있을 때만. 없으면 하지 않는다.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime
from typing import Any

from geoji_ai.core.logging import get_logger
from geoji_ai.telemetry import metrics as _metrics
from geoji_ai.telemetry.alerts import Alert, AlertNotifier, DailyCostTracker, is_eval_run
from geoji_ai.telemetry.logs import log_node

__all__ = [
    "count",
    "node_log",
    "notify",
    "observe",
    "rate",
    "track_cost",
    "utc_today",
]


def _failed(what: str, exc: BaseException) -> None:
    with contextlib.suppress(Exception):
        get_logger("geoji_ai.telemetry").warning(
            "instrument_failed", target=what, error_type=type(exc).__name__
        )


def count(name: str, value: float = 1, **labels: str) -> None:
    try:
        _metrics.REGISTRY.counter(name, value, **labels)
    except Exception as exc:
        _failed(name, exc)


def observe(name: str, value: float, **labels: str) -> None:
    try:
        _metrics.REGISTRY.histogram(name, value, **labels)
    except Exception as exc:
        _failed(name, exc)


def rate(name: str, hit: bool, **labels: str) -> None:
    try:
        _metrics.REGISTRY.rate(name, hit, **labels)
    except Exception as exc:
        _failed(name, exc)


def node_log(event: str, **fields: Any) -> None:
    try:
        log_node(event, **fields)
    except Exception as exc:
        _failed(event, exc)


async def notify(notifier: AlertNotifier | None, alert: Alert | None) -> None:
    if notifier is None or alert is None:
        return
    try:
        await notifier.notify(alert)
    except Exception as exc:
        _failed(str(alert.kind), exc)


def utc_today() -> date:
    """비용 집계의 "일". 계획서에 기준 시간대가 없어 UTC 날짜를 쓴다(보고서)."""
    return datetime.now(UTC).date()


async def track_cost(
    tracker: DailyCostTracker | None,
    notifier: AlertNotifier | None,
    micro_usd: int | None,
    *,
    day: date,
) -> None:
    """확정 비용을 일별 집계에 더하고, 서비스분이 임계에 닿으면 알린다. 비용 모름은 넣지 않는다."""
    if tracker is None or micro_usd is None:
        return
    try:
        tracker.add(micro_usd, is_eval=is_eval_run(), day=day)
        alert = tracker.check(day)
    except Exception as exc:
        _failed("daily_cost", exc)
        return
    await notify(notifier, alert)
