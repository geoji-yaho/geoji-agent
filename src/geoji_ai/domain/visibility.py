"""근거 공개 범위 판정(04 §3.4).

scope 모양은 10 §4.2 `scope{visibility, room_ids[]}` 이다. 계약 모델을 import 하지
않고 이 모듈의 작은 dataclass 로 받는다(어댑터가 변환한다).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Scope",
    "Visibility",
    "parse_visibility",
    "usable",
    "usable_for_share_card",
]


class Visibility(StrEnum):
    PUBLIC = "PUBLIC"
    ROOMS = "ROOMS"
    PRIVATE = "PRIVATE"


def parse_visibility(value: Visibility | str) -> Visibility:
    """`"ROOMS"` → `Visibility.ROOMS`. 모르는 값은 `ValueError`."""
    try:
        return Visibility(value)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 공개 범위: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class Scope:
    visibility: Visibility
    room_ids: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "visibility", parse_visibility(self.visibility))
        object.__setattr__(self, "room_ids", frozenset(self.room_ids))


def usable(scope: Scope, target_room_ids: Iterable[str]) -> bool:
    """이 근거를 target 방들에 가는 생성 문구에 써도 되는가.

    `PUBLIC` 은 항상, `ROOMS` 는 target 이 room_ids 의 부분집합일 때만,
    `PRIVATE` 는 생성용 pack 에서 뺀다. 빈 target 은 계획서에 없어 닫는다.
    """
    if scope.visibility is Visibility.PUBLIC:
        return True
    if scope.visibility is Visibility.ROOMS:
        target = frozenset(target_room_ids)
        return bool(target) and target <= scope.room_ids
    return False


def usable_for_share_card(scope: Scope) -> bool:
    """공유 카드에는 `PUBLIC` 근거만 쓴다."""
    return scope.visibility is Visibility.PUBLIC
