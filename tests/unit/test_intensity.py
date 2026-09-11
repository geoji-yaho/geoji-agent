"""강도 enum ↔ 표시명(01 §4.2)."""

from __future__ import annotations

import pytest

from geoji_ai.domain.intensity import (
    ALL_INTENSITIES,
    Intensity,
    display_name,
    from_display_name,
    parse_intensity,
)


def test_값은_프론트_소문자다():
    assert [member.value for member in Intensity] == ["mild", "spicy", "hell"]
    # 멤버 이름 = 값. 대문자 멤버(MILD·SPICY·HELL)를 만들지 않는다(D-21).
    assert [member.name for member in Intensity] == ["mild", "spicy", "hell"]


def test_모든_강도_3종():
    assert ALL_INTENSITIES == (Intensity.mild, Intensity.spicy, Intensity.hell)


@pytest.mark.parametrize("intensity", ALL_INTENSITIES)
def test_표시명_왕복(intensity: Intensity):
    assert from_display_name(display_name(intensity)) is intensity


def test_표시명은_한글_3종():
    assert [display_name(value) for value in ALL_INTENSITIES] == ["순한맛", "매운맛", "지옥맛"]


def test_문자열도_받는다():
    assert display_name("hell") == "지옥맛"
    assert parse_intensity("spicy") is Intensity.spicy


@pytest.mark.parametrize("value", ["HELL", "MILD", "extra-hot", "", "지옥맛"])
def test_알_수_없는_강도는_거부(value: str):
    with pytest.raises(ValueError):
        parse_intensity(value)
    with pytest.raises(ValueError):
        display_name(value)


@pytest.mark.parametrize("name", ["아주매운맛", "hell", "", "순한 맛"])
def test_알_수_없는_표시명은_거부(name: str):
    with pytest.raises(ValueError):
        from_display_name(name)
