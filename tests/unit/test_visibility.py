"""근거 공개 범위 판정(04 §3.4, §4.2)."""

from __future__ import annotations

import pytest

from geoji_ai.domain.visibility import Scope, Visibility, usable, usable_for_share_card


def test_ROOMS_A_근거는_AB_두_방_공통_문구에_쓰지_않는다():
    """먼저 실패시킬 케이스. A방 전용 기록을 B방까지 가는 문구에 주면 누출이다."""
    scope = Scope(visibility=Visibility.ROOMS, room_ids=frozenset({"A"}))
    assert usable(scope, {"A", "B"}) is False


def test_ROOMS_A_근거는_A방_문구에_쓴다():
    scope = Scope(visibility=Visibility.ROOMS, room_ids=frozenset({"A"}))
    assert usable(scope, {"A"}) is True


def test_ROOMS_근거는_target이_room_ids_부분집합이면_쓴다():
    scope = Scope(visibility="ROOMS", room_ids=frozenset({"A", "B", "C"}))
    assert usable(scope, {"A", "C"}) is True


def test_ROOMS_근거는_빈_target에_쓰지_않는다():
    """계획서에 없는 경계. 누출 방지 쪽으로 닫는다."""
    scope = Scope(visibility=Visibility.ROOMS, room_ids=frozenset({"A"}))
    assert usable(scope, set()) is False


@pytest.mark.parametrize("target", [set(), {"A"}, {"A", "B"}, {"Z"}])
def test_PUBLIC_근거는_항상_쓴다(target):
    scope = Scope(visibility=Visibility.PUBLIC, room_ids=frozenset())
    assert usable(scope, target) is True


@pytest.mark.parametrize("target", [set(), {"A"}, {"A", "B"}])
def test_PRIVATE_근거는_생성용_pack에서_뺀다(target):
    scope = Scope(visibility=Visibility.PRIVATE, room_ids=frozenset({"A"}))
    assert usable(scope, target) is False


@pytest.mark.parametrize(
    ("visibility", "expected"),
    [(Visibility.PUBLIC, True), (Visibility.ROOMS, False), (Visibility.PRIVATE, False)],
)
def test_공유_카드는_PUBLIC만(visibility, expected):
    scope = Scope(visibility=visibility, room_ids=frozenset({"A"}))
    assert usable_for_share_card(scope) is expected


def test_문자열_visibility를_enum으로_읽는다():
    scope = Scope(visibility="PUBLIC", room_ids=["A"])
    assert scope.visibility is Visibility.PUBLIC
    assert scope.room_ids == frozenset({"A"})


@pytest.mark.parametrize("value", ["public", "ALL", ""])
def test_모르는_visibility는_ValueError(value):
    with pytest.raises(ValueError):
        Scope(visibility=value, room_ids=frozenset())
