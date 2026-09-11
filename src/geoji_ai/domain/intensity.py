"""강도(순한맛·매운맛·지옥맛) 단일 정의.

한글 표시명은 **이 모듈에만** 둔다(01 §3.5, D-21). 다른 모듈에서 강도의 한글
문자열을 직접 쓰지 않고 `display_name()` 을 부른다.

enum 값은 프론트 정본(`geoji-web/src/shared/domain/room.ts`)과 같은 소문자다.
멤버 이름 = 값이므로 `Intensity.hell` 이고 `Intensity.HELL` 은 없다.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ALL_INTENSITIES",
    "Intensity",
    "display_name",
    "from_display_name",
    "parse_intensity",
]


class Intensity(StrEnum):
    mild = "mild"
    spicy = "spicy"
    hell = "hell"


ALL_INTENSITIES: tuple[Intensity, ...] = (Intensity.mild, Intensity.spicy, Intensity.hell)

# 표시명 단일 지점. 이 dict 밖에서 순한맛·매운맛·지옥맛 문자열을 쓰지 않는다.
_DISPLAY_NAMES: dict[Intensity, str] = {
    Intensity.mild: "순한맛",
    Intensity.spicy: "매운맛",
    Intensity.hell: "지옥맛",
}

_BY_DISPLAY_NAME: dict[str, Intensity] = {name: value for value, name in _DISPLAY_NAMES.items()}


def parse_intensity(value: str) -> Intensity:
    """`"hell"` → `Intensity.hell`. 모르는 값은 `ValueError`."""
    try:
        return Intensity(value)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 강도: {value!r}") from exc


def display_name(intensity: Intensity | str) -> str:
    """강도 → 한글 표시명. 모르는 값은 `ValueError`."""
    return _DISPLAY_NAMES[parse_intensity(intensity)]


def from_display_name(name: str) -> Intensity:
    """한글 표시명 → 강도. 모르는 값은 `ValueError`."""
    try:
        return _BY_DISPLAY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 강도 표시명: {name!r}") from exc
