"""반복 집계 규칙과 aggregation 메타(04 §1·§3.4, 10 §4.2).

같은 카테고리 확정 소비(`post_type == "spent"`)를 현재 사건 생성 시각 이전
30일 창 `[created_at − 30일, created_at)` 에서 센다. 현재 사건은 뺀다.
판결 확정 여부는 조건이 아니다. 항목 단위 숫자는 만들지 않는다(04 §1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

__all__ = [
    "SPENT",
    "WINDOW",
    "AggregationMeta",
    "CurrentCase",
    "PastPost",
    "build_aggregation",
    "check_backend_aggregates",
    "count_repeat_same_category",
    "validate_aggregation",
]

#: 집계 창 길이(04 §1 "이전 30일").
WINDOW: timedelta = timedelta(days=30)

#: 확정 소비 게시물 유형. `considering` 은 세지 않는다.
SPENT = "spent"

_META_KEYS = ("rule_version", "start_at", "end_at", "excludes_post_id")
_BACKEND_KEYS = ("repeat_same_category_30d", "excludes_post_id", "window", "rule_version")
_WINDOW_KEYS = ("start_at", "end_at")


def _require_aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} 는 datetime 이어야 한다: {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} 는 시간대가 있어야 한다: {value!r}")
    return value


def _parse_time(value: Any, field: str) -> datetime:
    """datetime 이나 RFC3339 문자열을 tz-aware datetime 으로."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} 를 시각으로 읽지 못했다: {value!r}") from exc
    return _require_aware(value, field)


@dataclass(frozen=True, slots=True)
class CurrentCase:
    post_id: str
    category: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class PastPost:
    post_id: str
    category: str
    post_type: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class AggregationMeta:
    rule_version: int
    start_at: datetime
    end_at: datetime
    excludes_post_id: str

    def to_mapping(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in _META_KEYS}


def count_repeat_same_category(current: CurrentCase, past: Iterable[PastPost]) -> int:
    """현재 사건과 같은 카테고리 확정 소비가 창 안에 몇 건인가."""
    start_at = current.created_at - WINDOW
    return sum(
        1
        for post in past
        if post.post_id != current.post_id
        and post.category == current.category
        and post.post_type == SPENT
        and start_at <= post.created_at < current.created_at
    )


def build_aggregation(current: CurrentCase, rule_version: int) -> AggregationMeta:
    """현재 사건 기준 aggregation 메타."""
    return AggregationMeta(
        rule_version=rule_version,
        start_at=current.created_at - WINDOW,
        end_at=current.created_at,
        excludes_post_id=current.post_id,
    )


def _require_keys(data: Mapping[str, Any], keys: Iterable[str], where: str) -> None:
    missing = [key for key in keys if data.get(key) is None]
    if missing:
        raise ValueError(f"{where} 필수 필드가 없다: {', '.join(missing)}")


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 는 정수여야 한다: {value!r}")
    return value


def validate_aggregation(meta: Mapping[str, Any]) -> None:
    """aggregation 메타 4키가 모두 있고 창이 앞뒤가 맞는지. 아니면 `ValueError`."""
    _require_keys(meta, _META_KEYS, "aggregation")
    _require_int(meta["rule_version"], "rule_version")
    start_at = _parse_time(meta["start_at"], "start_at")
    end_at = _parse_time(meta["end_at"], "end_at")
    if not start_at < end_at:
        raise ValueError(f"start_at 이 end_at 보다 앞서야 한다: {start_at} >= {end_at}")


def check_backend_aggregates(aggregates: Mapping[str, Any], current: CurrentCase) -> None:
    """백엔드가 준 `aggregates`(10 §4.2) 가 이 규칙대로 셌는지. 아니면 `ValueError`."""
    _require_keys(aggregates, _BACKEND_KEYS, "aggregates")
    window = aggregates["window"]
    if not isinstance(window, Mapping):
        raise ValueError(f"aggregates.window 는 객체여야 한다: {window!r}")
    _require_keys(window, _WINDOW_KEYS, "aggregates.window")
    _require_int(aggregates["rule_version"], "rule_version")
    count = _require_int(aggregates["repeat_same_category_30d"], "repeat_same_category_30d")

    problems: list[str] = []
    if count < 0:
        problems.append(f"repeat_same_category_30d 가 음수다: {count}")
    if aggregates["excludes_post_id"] != current.post_id:
        problems.append(
            f"excludes_post_id 가 현재 post 가 아니다: {aggregates['excludes_post_id']!r}"
        )
    start_at = _parse_time(window["start_at"], "window.start_at")
    end_at = _parse_time(window["end_at"], "window.end_at")
    if end_at != current.created_at:
        problems.append(f"window.end_at 이 현재 created_at 이 아니다: {end_at}")
    if end_at - start_at != WINDOW:
        problems.append(f"window 길이가 {WINDOW} 가 아니다: {end_at - start_at}")
    if problems:
        raise ValueError("; ".join(problems))
