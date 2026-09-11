"""어휘 목록과 강도별 적용 표(01 §4.2)."""

from __future__ import annotations

import pytest

from geoji_ai.domain.intensity import ALL_INTENSITIES, Intensity
from geoji_ai.domain.lexicon import (
    DEATH_WORDS,
    HELL_ALLOWED_PROFANITY,
    HELL_ONCE_PER_VERDICT,
    ID_IN_TEXT,
    PROFANITY,
    WORN_PHRASES,
    LexiconRule,
    applies,
)


def test_목록이_계획서_그대로다():
    # 01 §3.5 에 적힌 것 그대로. 늘리거나 줄이지 않는다.
    assert DEATH_WORDS == (
        "자살",
        "자해",
        "죽어",
        "죽고 싶",
        "죽여",
        "뒤져",
        "뒤지",
        "목을 매",
        "손목",
        "극단적 선택",
    )
    assert len(PROFANITY) == 24
    assert PROFANITY[0] == "미친"
    assert PROFANITY[-1] == "멍청"
    assert set(PROFANITY) >= {"씨발", "ㅅㅂ", "ㅂㅅ", "존나", "ㅈㄴ", "좆", "또라이", "등신"}
    assert HELL_ALLOWED_PROFANITY == (
        "미친",
        "돌았냐",
        "정신 나갔냐",
        "실화냐",
        "어이없네",
        "개같은 선택",
        "지랄",
        "꼴",
        "처타다",
        "헛소리",
        "레전드",
        "새끼",
    )
    assert HELL_ONCE_PER_VERDICT == ("새끼", "ㅋㅋ")
    # 01 §3.5 가 "정신 차리십시오 등"으로 끝난다. 나머지는 계획서에 없어 비워 둔다.
    assert WORN_PHRASES == ("정신 차리십시오",)


@pytest.mark.parametrize(
    "rule",
    [LexiconRule.DEATH_WORDS, LexiconRule.WORN_PHRASES, LexiconRule.ID_IN_TEXT],
)
@pytest.mark.parametrize("intensity", ALL_INTENSITIES)
def test_모든_강도에_켜지는_검사(rule: LexiconRule, intensity: Intensity):
    assert applies(intensity, rule) is True


def test_비속어_0개_검사는_순한맛_매운맛만():
    assert applies(Intensity.mild, LexiconRule.PROFANITY) is True
    assert applies(Intensity.spicy, LexiconRule.PROFANITY) is True
    assert applies(Intensity.hell, LexiconRule.PROFANITY) is False


@pytest.mark.parametrize(
    "rule", [LexiconRule.HELL_ALLOWED_PROFANITY, LexiconRule.HELL_ONCE_PER_VERDICT]
)
def test_허용_목록_검사는_지옥맛만(rule: LexiconRule):
    assert applies(Intensity.hell, rule) is True
    assert applies(Intensity.mild, rule) is False
    assert applies(Intensity.spicy, rule) is False


def test_문자열로도_부른다():
    assert applies("spicy", "PROFANITY") is True
    assert applies("hell", "HELL_ALLOWED_PROFANITY") is True


@pytest.mark.parametrize(
    ("intensity", "rule"),
    [("HELL", "PROFANITY"), ("hell", "PROFANITIES"), ("무매운맛", "DEATH_WORDS")],
)
def test_알_수_없는_값은_거부(intensity: str, rule: str):
    with pytest.raises(ValueError):
        applies(intensity, rule)


def test_ID_IN_TEXT_는_본문에_샌_라벨을_잡는다():
    text = "F0 에 따르면 지하철 8회분이다. F12 도 같다."
    assert ID_IN_TEXT.findall(text) == ["F0", "F12"]
    assert ID_IN_TEXT.search("라벨이 없는 판결문입니다.") is None
    # 단어 경계가 있어야 한다. 단어 안의 F 는 잡지 않는다.
    assert ID_IN_TEXT.search("AF1") is None
