"""반복 집계 규칙과 aggregation 메타(04 §1·§3.4·§4.1·§4.2, 10 §4.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from geoji_ai.domain import aggregation
from geoji_ai.domain.aggregation import (
    WINDOW,
    AggregationMeta,
    CurrentCase,
    PastPost,
    build_aggregation,
    check_backend_aggregates,
    count_repeat_same_category,
    validate_aggregation,
)

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
CURRENT = CurrentCase(post_id="p-now", category="food", created_at=NOW)


def _past(post_id: str, delta: timedelta, *, category: str = "food", post_type: str = "spent"):
    return PastPost(post_id=post_id, category=category, post_type=post_type, created_at=NOW - delta)


def test_현재_post는_반복_집계에서_뺀다():
    """먼저 실패시킬 케이스. 현재 사건을 세면 "이번 포함 N회"가 된다."""
    same_as_current = PastPost(
        post_id="p-now", category="food", post_type="spent", created_at=NOW - timedelta(hours=1)
    )
    other = _past("p-1", timedelta(days=1))
    assert count_repeat_same_category(CURRENT, [same_as_current, other]) == 1


def test_창은_30일이다():
    assert timedelta(days=30) == WINDOW


@pytest.mark.parametrize(
    ("delta", "counted"),
    [
        (timedelta(days=29), True),
        (timedelta(days=30), True),  # start_at 정각은 포함
        (timedelta(days=30, microseconds=1), False),
        (timedelta(days=31), False),
        (timedelta(minutes=1), True),
        (timedelta(0), False),  # 현재 created_at 정각은 끝 제외
        (-timedelta(minutes=1), False),  # 같은 날 사건 생성 뒤
    ],
)
def test_창_경계(delta, counted):
    assert count_repeat_same_category(CURRENT, [_past("p-1", delta)]) == (1 if counted else 0)


def test_같은_카테고리만_센다():
    posts = [
        _past("p-1", timedelta(days=1)),
        _past("p-2", timedelta(days=2), category="taxi"),
        _past("p-3", timedelta(days=3)),
    ]
    assert count_repeat_same_category(CURRENT, posts) == 2


def test_확정_소비만_센다():
    posts = [
        _past("p-1", timedelta(days=1)),
        _past("p-2", timedelta(days=2), post_type="considering"),
    ]
    assert count_repeat_same_category(CURRENT, posts) == 1


def test_naive_datetime은_받지_않는다():
    with pytest.raises(ValueError):
        CurrentCase(post_id="p", category="food", created_at=datetime(2026, 9, 14, 12, 0))
    with pytest.raises(ValueError):
        PastPost(
            post_id="p",
            category="food",
            post_type="spent",
            created_at=datetime(2026, 9, 14, 12, 0),
        )


def test_메타는_창과_제외_post를_담는다():
    meta = build_aggregation(CURRENT, rule_version=3)
    assert meta == AggregationMeta(
        rule_version=3,
        start_at=NOW - timedelta(days=30),
        end_at=NOW,
        excludes_post_id="p-now",
    )
    validate_aggregation(meta.to_mapping())


@pytest.mark.parametrize("key", ["rule_version", "start_at", "end_at", "excludes_post_id"])
def test_메타_필수_키가_빠지면_오류(key):
    data = dict(build_aggregation(CURRENT, rule_version=1).to_mapping())
    del data[key]
    with pytest.raises(ValueError):
        validate_aggregation(data)


@pytest.mark.parametrize("key", ["rule_version", "start_at", "end_at", "excludes_post_id"])
def test_메타_필수_키가_None이면_오류(key):
    data = dict(build_aggregation(CURRENT, rule_version=1).to_mapping())
    data[key] = None
    with pytest.raises(ValueError):
        validate_aggregation(data)


def test_메타_start_at이_end_at보다_늦으면_오류():
    data = dict(build_aggregation(CURRENT, rule_version=1).to_mapping())
    data["start_at"], data["end_at"] = data["end_at"], data["start_at"]
    with pytest.raises(ValueError):
        validate_aggregation(data)


def _backend(**overrides):
    data = {
        "repeat_same_category_30d": 2,
        "excludes_post_id": "p-now",
        "window": {"start_at": "2026-08-15T12:00:00Z", "end_at": "2026-09-14T12:00:00Z"},
        "rule_version": 1,
    }
    data.update(overrides)
    return data


def test_백엔드_aggregates가_규칙과_맞으면_통과():
    check_backend_aggregates(_backend(), CURRENT)


def test_백엔드_aggregates가_현재_post를_빼지_않았으면_오류():
    with pytest.raises(ValueError):
        check_backend_aggregates(_backend(excludes_post_id="p-other"), CURRENT)


def test_백엔드_aggregates_창_끝이_현재_created_at이_아니면_오류():
    window = {"start_at": "2026-08-16T12:00:00Z", "end_at": "2026-09-15T12:00:00Z"}
    with pytest.raises(ValueError):
        check_backend_aggregates(_backend(window=window), CURRENT)


def test_백엔드_aggregates_창_길이가_30일이_아니면_오류():
    window = {"start_at": "2026-08-14T12:00:00Z", "end_at": "2026-09-14T12:00:00Z"}
    with pytest.raises(ValueError):
        check_backend_aggregates(_backend(window=window), CURRENT)


@pytest.mark.parametrize(
    "key", ["repeat_same_category_30d", "excludes_post_id", "window", "rule_version"]
)
def test_백엔드_aggregates_필드가_빠지면_오류(key):
    data = _backend()
    del data[key]
    with pytest.raises(ValueError):
        check_backend_aggregates(data, CURRENT)


@pytest.mark.parametrize("key", ["start_at", "end_at"])
def test_백엔드_aggregates_window_필드가_빠지면_오류(key):
    data = _backend()
    del data["window"][key]
    with pytest.raises(ValueError):
        check_backend_aggregates(data, CURRENT)


def test_항목_단위_숫자를_만드는_함수는_없다():
    """항목 숫자("택시 3회")는 만들지 않는다(04 §1)."""
    names = [name.lower() for name in dir(aggregation) if not name.startswith("_")]
    assert not [name for name in names if "item" in name or "항목" in name]
