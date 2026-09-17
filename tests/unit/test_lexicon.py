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
@pytest.mark.parametrize("intensity", ALL_INTENSITIES)
def test_지옥맛_허용_목록_검사는_어느_강도에도_켜지지_않는다(
    rule: LexiconRule, intensity: Intensity
):
    """9/16 결정: 지옥맛은 비속어를 차단하지 않는다.

    (원문) hell 만 켜서 12개 허용 목록 밖을 `PROFANITY_OUT_OF_LIST` 로 잡았다. 목록이 어간·표층형
    혼합이라 허용 단어의 활용형까지 막혔다(`미쳤네`·`돌았어`·`처먹네`).
    """
    assert applies(intensity, rule) is False


def test_자해_죽음_어휘는_지옥맛에서도_막는다():
    """비속어 차단은 껐지만 안전 규칙은 전 강도 그대로다."""
    assert applies(Intensity.hell, LexiconRule.DEATH_WORDS) is True


def test_문자열로도_부른다():
    assert applies("spicy", "PROFANITY") is True
    assert applies("hell", "HELL_ALLOWED_PROFANITY") is False


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
